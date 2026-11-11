# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from activitytrack import sync
from activitytrack.store import Store
from activitytrack.sync import SyncEngine, SyncIncompatible, SyncRevoked

from .fake_server import FakeServer


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "data.sqlite3")
        self.t = 1_000
        self.server = FakeServer(now=lambda: self.t + 5)
        self.engine = SyncEngine(self.store, self.server, clock=lambda: self.t)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_the_first_sync_pulls_everything(self):
        self.server.put("company", "c1", {"name": "Acme"}, central=True)
        self.server.put("todo", "t1", {"title": "Call", "companyUid": "c1"})
        result = self.engine.sync()
        self.assertEqual(result.pulled, 2)
        self.assertEqual(self.store.get("todo", "t1")["title"], "Call")
        self.assertTrue(self.store.get("company", "c1")["_central"])
        self.assertEqual(self.store.cursor, 2)
        self.assertEqual(self.store.get_state("offset"), 5)

    def test_a_local_change_is_pushed_and_settled(self):
        self.store.write("entry", "e1", {"activity": "Patch", "status": "ACTIVE"}, now_ms=900)
        self.assertTrue(self.store.get("entry", "e1")["_pending"])
        self.assertEqual(self.store.pending_count(), 1)
        result = self.engine.sync()
        self.assertEqual(result.pushed, 1)
        self.assertFalse(self.store.has_pending())
        self.assertEqual(self.store.pending_count(), 0)
        self.assertEqual(self.server.records[("entry", "e1")]["fields"]["activity"], "Patch")
        self.assertEqual(self.server.records[("entry", "e1")]["clock"]["status"], 900)
        e = self.store.get("entry", "e1")
        self.assertFalse(e["_pending"])
        self.assertEqual(e["status"], "ACTIVE")

    def test_field_by_field_the_last_writer_wins(self):
        self.server.put("entry", "e1", {"activity": "A", "notes": "server"}, at=500)
        self.engine.sync()
        self.store.write("entry", "e1", {"activity": "B"}, now_ms=400)
        self.store.write("entry", "e1", {"locality": "Roma"}, now_ms=600)
        self.engine.sync()
        e = self.store.get("entry", "e1")
        self.assertEqual((e["activity"], e["locality"], e["notes"]), ("A", "Roma", "server"))
        self.assertFalse(self.store.has_pending())

    def test_a_field_changed_while_the_push_travels_stays_pending(self):
        self.store.write("entry", "e1", {"activity": "A"}, now_ms=100)
        row = self.store.pending(10)[0]
        self.store.write("entry", "e1", {"activity": "B"}, now_ms=200)
        self.store.pushed(row, {"status": "applied", "record": {
            "type": "entry", "uid": "e1", "rev": 1, "fields": {"activity": "A"}}})
        self.assertEqual(self.store.get("entry", "e1")["activity"], "B")
        self.assertEqual(self.store.change(self.store.pending(10)[0])["fields"], {"activity": "B"})

    def test_a_pull_does_not_hide_a_pending_field(self):
        self.store.write("todo", "t1", {"title": "mine"}, now_ms=100)
        self.store.pulled({"type": "todo", "uid": "t1", "rev": 3, "fields": {"title": "theirs", "notes": "n"}})
        t = self.store.get("todo", "t1")
        self.assertEqual((t["title"], t["notes"]), ("mine", "n"))

    def test_deletes(self):
        self.server.put("todo", "t1", {"title": "x"})
        self.engine.sync()
        self.store.delete("todo", "t1", now_ms=100)
        self.assertIsNone(self.store.get("todo", "t1"))
        self.engine.sync()
        self.assertTrue(self.server.records[("todo", "t1")]["deleted"])
        self.assertFalse(self.store.has_pending())
        # a record the server never had just disappears
        self.store.write("todo", "t2", {"title": "y"}, now_ms=100)
        self.store.delete("todo", "t2", now_ms=110)
        self.assertFalse(self.store.has_pending())

    def test_a_record_deleted_there_drops_the_change_here(self):
        self.server.put("todo", "t1", {"title": "x"})
        self.engine.sync()
        self.store.write("todo", "t1", {"title": "y"}, now_ms=100)
        self.server.records[("todo", "t1")].update(deleted=True, fields={})
        self.server.rev += 1
        self.server.records[("todo", "t1")]["rev"] = self.server.rev
        self.engine.sync()
        self.assertIsNone(self.store.get("todo", "t1"))
        self.assertFalse(self.store.has_pending())

    def test_a_record_out_of_scope_goes(self):
        self.server.put("todo", "t1", {"title": "x"})
        self.engine.sync()
        self.server.leave("todo", "t1")
        self.engine.sync()
        self.assertIsNone(self.store.get("todo", "t1"))
        self.assertEqual(self.store.all("todo"), [])

    def test_a_refused_change_stays_and_is_listed(self):
        self.server.refuse.add("company")
        self.store.write("company", "c1", {"name": "Mine"}, now_ms=100)
        result = self.engine.sync()
        self.assertEqual(result.pushed, 0)
        self.assertIn("permission", result.errors[0])
        self.assertTrue(self.store.has_pending())
        self.assertEqual(self.store.errors()[0]["detail"], "locked")
        self.server.refuse.clear()
        self.engine.sync()
        self.assertEqual(self.store.errors(), [])

    def test_pages(self):
        for i in range(sync.BATCH + 20):
            self.server.put("tag", f"g{i}", {"name": str(i)})
        result = self.engine.sync()
        self.assertEqual(result.pulled, sync.BATCH + 20)
        self.assertEqual(len(self.store.all("tag")), sync.BATCH + 20)
        pulls = [p for _, p, _ in self.server.requests if p.startswith("sync/pull")]
        self.assertEqual(pulls[:2], [
            f"sync/pull?cursor=0&limit={sync.BATCH}", f"sync/pull?cursor={sync.BATCH}&limit={sync.BATCH}"])

    def test_revoked_and_incompatible(self):
        self.store.write("todo", "t1", {"title": "x"}, now_ms=100)
        self.server.status = 401
        with self.assertRaises(SyncRevoked):
            self.engine.sync()
        self.assertEqual(self.store.get_state("last_error"), "revoked")
        self.server.status = 426
        with self.assertRaises(SyncIncompatible) as e:
            self.engine.sync()
        self.assertEqual(e.exception.code, "app_too_old")
        # the data stay, ready for when the versions match again
        self.assertTrue(self.store.has_pending())
        self.server.status = None
        self.engine.sync()
        self.assertFalse(self.store.has_pending())


class UpgradeRequiredTest(unittest.TestCase):
    """The server of schema 3 refuses an older client with `upgrade_required` (ACTIVITY-MODEL.md §6)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.server = FakeServer()

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_the_schema_is_3(self):
        from activitytrack import wire
        self.assertEqual(wire.SCHEMA, 3)

    def test_whatever_the_status(self):
        from activitytrack import wire
        for status in (426, 400, 409):
            self.server.status, self.server.error = status, "upgrade_required"
            with self.assertRaises(SyncIncompatible) as e:
                SyncEngine(self.store, self.server).sync()
            self.assertEqual(e.exception.code, wire.UPGRADE_REQUIRED)
        self.assertEqual(self.store.get_state("last_error"), "incompatible: upgrade_required")

    def test_the_runner_states_and_says_what_to_do(self):
        from activitytrack import texts
        from activitytrack.sync import Runner
        r = Runner(_Session(self.store, self.server))
        self.server.status, self.server.error = 426, "upgrade_required"
        self.assertEqual(r.run(), ("incompatible", "upgrade_required"))
        self.assertEqual(self.store.get_state("incompatible"), "upgrade_required")
        self.assertEqual(r.refresh_update(), "incompatible:upgrade_required")
        paired = types.SimpleNamespace(store=self.store, account=object())
        self.assertEqual(texts.sync_state(paired, "incompatible"), "Aggiorna l'app per sincronizzare")
        self.assertTrue(texts.sync_needs_attention(paired))
        # a refusal of another kind stays what it was
        self.server.status, self.server.error = 500, "boom"
        self.assertEqual(r.run()[0], "failed")

    def test_translated(self):
        from activitytrack import i18n, texts
        try:
            i18n.set_language("en")
            self.assertEqual(i18n._(texts.UPGRADE_REQUIRED), "Update the app to sync")
            i18n.set_language("de")
            self.assertNotEqual(i18n._(texts.UPGRADE_REQUIRED), texts.UPGRADE_REQUIRED)
        finally:
            i18n.set_language("it")


class _Session:
    def __init__(self, store, http):
        self.store, self._http = store, http

    def http(self):
        return self._http


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store_file = Path(self.dir.name) / "data.sqlite3"
        self.store = Store(self.store_file)
        self.server = FakeServer()

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_the_manifest_of_the_server_is_kept_at_each_sync(self):
        from activitytrack.sync import Runner
        r = Runner(_Session(self.store, self.server))
        r.run()
        self.assertIsNone(self.store.get_state("update.manifest"))
        self.server.update = '{"payload": "x"}'
        r.run()
        self.assertEqual(self.store.get_state("update.manifest"), '{"payload": "x"}')
        self.server.put("todo", "t1", {"title": "x"})
        r.run(full=False)
        self.assertEqual(self.store.get_state("update.manifest"), '{"payload": "x"}')
        # an older server says nothing: what is there stays; a null or an absurd value clears it
        self.server.update = ...
        r.run()
        self.assertIsNotNone(self.store.get_state("update.manifest"))
        self.server.update = None
        r.run()
        self.assertIsNone(self.store.get_state("update.manifest"))
        self.server.update = "x" * (2 * 1024 * 1024)
        r.run()
        self.assertIsNone(self.store.get_state("update.manifest"))
        self.assertEqual("", r.refresh_update())
        self.server.status = 401
        self.assertEqual("revoked", r.refresh_update())
        self.server.status = 426
        self.assertEqual("incompatible:app_too_old", r.refresh_update())
        self.assertEqual("app_too_old", self.store.get_state("incompatible"))

    def test_the_head_is_asked_once_a_run(self):
        from activitytrack.sync import Runner
        r = Runner(_Session(self.store, self.server))
        r.run()
        self.server.put("todo", "t1", {"title": "x"})
        before = len(self.server.requests)
        # nothing pending, the revision moved: the head that tells so is the one that tells of the update
        self.assertEqual(("ok", ""), r.run(full=False))
        heads = [m for m in self.server.requests[before:] if m[1] == "sync/head"]
        self.assertEqual(1, len(heads))
        before = len(self.server.requests)
        r.run(full=True)
        self.assertEqual(1, len([m for m in self.server.requests[before:] if m[1] == "sync/head"]))

    def test_the_states(self):
        from activitytrack.net import NetworkError
        from activitytrack.sync import Runner
        self.assertEqual(Runner(_Session(self.store, None)).run(), ("unpaired", ""))
        r = Runner(_Session(self.store, self.server))
        self.server.put("todo", "t1", {"title": "x"})
        self.assertEqual(r.run(full=False), ("ok", ""))
        # nothing moved: head only
        self.assertEqual(r.run(full=False), ("idle", ""))
        self.server.status = 426
        self.assertEqual(r.run(), ("incompatible", "app_too_old"))
        self.assertEqual(self.store.get_state("incompatible"), "app_too_old")
        self.server.status = 401
        self.assertEqual(r.run(), ("revoked", ""))
        self.assertTrue(self.store.get_state("revoked"))

        class Down:
            def send(self, *a):
                raise NetworkError("no route")
        self.assertEqual(Runner(_Session(self.store, Down())).run(), ("offline", "no route"))

    def test_an_account_that_cannot_connect_is_a_state(self):
        from activitytrack.net import NetworkError
        from activitytrack.sync import Runner

        class Refused(_Session):
            def http(self):
                raise NetworkError("not https")
        # not an exception in the caller's thread
        self.assertEqual(Runner(Refused(self.store, None)).run(), ("failed", "not https"))

    def test_an_offline_run_does_not_touch_the_data(self):
        from activitytrack.net import NetworkError
        from activitytrack.sync import Runner

        class Down:
            def send(self, *a):
                raise NetworkError("no route")
        r = Runner(_Session(self.store, Down()))
        self.store.set_state("x", 1)
        r.run()
        other = sqlite3.connect(self.store_file)
        before = other.execute("PRAGMA data_version").fetchone()[0]
        # the same error again writes nothing: the other interfaces see no change
        self.assertEqual(r.run(), ("offline", "no route"))
        self.assertEqual(other.execute("PRAGMA data_version").fetchone()[0], before)
        self.store.set_state("x", 2)
        self.assertNotEqual(other.execute("PRAGMA data_version").fetchone()[0], before)
        other.close()
