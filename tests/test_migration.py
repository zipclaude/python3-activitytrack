# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from activitytrack import migration as m
from activitytrack.store import MODEL, Store
from activitytrack.sync import SyncEngine, SyncFailed

from .fake_server import FakeServer

DATA = Path(__file__).with_name("data")
DOC = json.loads((DATA / "activity-migration.json").read_text(encoding="utf-8"))

# the app's tables as the store's types, and what each has in the wire
TYPES = {"time_entry": ("entry", m.ENTRY_FIELDS), "segment": ("segment", m.SEGMENT_FIELDS),
         "external_ref": ("externalRef", m.REF_FIELDS), "todo": ("todo", m.TODO_FIELDS),
         "absence": ("absence", m.ABSENCE_FIELDS), "work_schedule": ("workSchedule", m.SCHEDULE_FIELDS),
         "lunch_break": ("lunchBreak", m.SCHEDULE_FIELDS), "day_off": ("dayOff", m.DAY_OFF_FIELDS)}


def records_of(tables):
    """The cases' tables as the store's records: {type: {uid: wire fields}}."""
    uid_of = {t: {r["id"]: r.get("uid") or f"{t}-{r['id']}" for r in tables.get(t, []) if "id" in r}
              for t in ("time_entry", "todo")}
    out = {}
    for table, rows in tables.items():
        type_, spec = TYPES[table]
        found = out.setdefault(type_, {})
        for n, r in enumerate(rows):
            uid = r.get("uid") or f"{table}-{r.get('id', n)}"
            fields = m._fields(spec, r)
            if table == "time_entry" and r.get("todo_id") is not None:
                fields["todoUid"] = uid_of["todo"].get(r["todo_id"], f"missing-{r['todo_id']}")
            if "entry_id" in r:
                fields["entryUid"] = uid_of["time_entry"][r["entry_id"]]
            found[uid] = fields
    return out


def expected_of(after):
    """A case's `after` as records: {type: {uid: fields}}, only what the wire carries."""
    out = {"entry": {}, "segment": {}, "externalRef": {}}
    for table, type_ in (("time_entry", "entry"), ("segment", "segment"), ("external_ref", "externalRef")):
        spec = TYPES[table][1]
        for r in after[table]:
            row = dict(r)
            if "entry_uid" in row:
                entry_uid = row.pop("entry_uid")
            if isinstance(row.get("plugin_fields"), (dict, list)):
                row["plugin_fields"] = json.dumps(row["plugin_fields"], ensure_ascii=False)
            fields = {n: m._to_wire(row.get(c), conv) for n, c, conv in spec}
            if table != "time_entry":
                fields["entryUid"] = entry_uid
            else:
                fields["tags"] = []
            out[type_][r["uid"]] = fields
    return out


class ReferenceCasesTest(unittest.TestCase):
    """The shared cases, through the reference as the app's repository has it."""

    def test_enough_cases(self):
        self.assertGreaterEqual(len(DOC["cases"]), 30)

    def test_cases(self):
        for case in DOC["cases"]:
            with self.subTest(case["name"]):
                before = copy.deepcopy(case["before"])
                got = m.normalize(m.migrate(before, case["zone"]))
                self.assertEqual(json.loads(json.dumps(got, sort_keys=True)),
                                 json.loads(json.dumps(case["after"], sort_keys=True)))
                self.assertEqual(before, case["before"], "migrate must not modify its input")

    def test_deterministic_and_unique_uids(self):
        for case in DOC["cases"]:
            a = m.migrate(case["before"], case["zone"])
            self.assertEqual(a, m.migrate(case["before"], case["zone"]))
            for table in ("time_entry", "external_ref"):
                uids = [r["uid"] for r in a[table]]
                self.assertEqual(len(uids), len(set(uids)), case["name"])

    def test_invariants(self):
        for case in DOC["cases"]:
            for e in m.migrate(case["before"], case["zone"])["time_entry"]:
                self.assertNotIn("todo_id", e)
                self.assertIn(e["status"], ("TODO", "RUNNING", "CLOSED"))
                self.assertTrue(0 <= e["priority"] <= 5)
                self.assertEqual(e["kind"] == "ABSENCE", e["absence_kind"] is not None)

    def test_helpers(self):
        priorities = [m.map_priority(p) for p in (None, 1, 2, 3, 4, 5, 6, 7, 0, -1)]
        self.assertEqual(priorities, [0, 5, 4, 3, 2, 1, 1, 1, 0, 0])
        self.assertEqual(m.classify_ref(" https://a.b/c "), "URL")
        self.assertEqual(m.absence_kind(" ferie "), "HOLIDAY")
        # Kotlin's trim(): Python's whitespace but U+0085; lower case, not case folding (#22)
        self.assertEqual(set(m._WS), {c for c in map(chr, range(0x110000)) if c.isspace()} - {"\x85"})
        self.assertEqual(m.absence_kind("\u00a0Ferie\u3000"), "HOLIDAY")
        self.assertEqual(m.absence_kind("Ferie\x85"), "ABSENCE")
        self.assertEqual(m.absence_kind("\u017fick leave"), "ABSENCE")


class RecordCasesTest(unittest.TestCase):
    """The same cases through the library's adapter: the store's wire records in, the records of schema 3 out."""

    def test_cases(self):
        for case in DOC["cases"]:
            with self.subTest(case["name"]):
                got = m.migrate_records(records_of(case["before"]), case["zone"])
                want = expected_of(case["after"])
                for type_ in m.WRITES:
                    self.assertEqual(got[type_], want[type_], type_)

    def test_the_priority_scale_agrees_with_the_model(self):
        from activitytrack import model
        for p in (None, 1, 2, 3, 4, 5, 6, 99, 0, -1):
            self.assertEqual(m.map_priority(p), model.priority_of_legacy(p))


class BlankKeyTest(unittest.TestCase):
    def test_a_blank_external_key_is_no_key(self):
        todo = {"title": "x", "done": False, "createdUtcMs": 1, "updatedUtcMs": 1, "kind": "TASK"}
        out = m.migrate_records({"todo": {"a": dict(todo, externalKey=""), "b": dict(todo, externalKey=" "),
                                          "c": dict(todo, externalKey="K")}})
        self.assertEqual({u: f["externalKey"] for u, f in out["entry"].items()}, {"a": None, "b": None, "c": "K"})

    def test_a_blank_key_from_the_server_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "s.sqlite3")
            s.pulled({"type": "entry", "uid": "e", "rev": 1, "fields": {"status": "TODO", "externalKey": ""}})
            self.assertIsNone(s.get("entry", "e")["externalKey"])
            s.close()


class StoreMigrationTest(unittest.TestCase):
    ZONE = "Europe/Rome"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "d.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        s = self.store
        pulled = s.pulled
        for type_, uid, fields in (
            ("client", "c1", {"name": "Acme", "archived": False}),
            ("todo", "t1", {"title": "Ticket", "refs": "INC1", "priority": 2, "done": False,
                            "planDate": "2026-11-12", "pluginFields": {"a": 1}, "createdUtcMs": 5,
                            "updatedUtcMs": 6, "kind": "TASK"}),
            ("todo", "t2", {"title": "Chiamare", "done": True, "doneUtcMs": 900, "createdUtcMs": 7,
                            "updatedUtcMs": 8}),
            ("entry", "e1", {"status": "TO_COMPLETE", "mode": None, "activity": "Rete", "clientUid": "c1",
                             "todoUid": "t1", "createdUtcMs": 10, "updatedUtcMs": 11, "needsReview": False,
                             "tags": ["x"], "zoneId": self.ZONE}),
            ("entry", "e2", {"status": "ACTIVE", "mode": "WORK", "activity": "Cavi", "clientUid": "c1",
                             "createdUtcMs": 12, "updatedUtcMs": 13, "needsReview": False, "zoneId": self.ZONE}),
            ("segment", "s1", {"entryUid": "e1", "kind": "WORK", "startUtcMs": 100, "endUtcMs": 400,
                               "zoneId": self.ZONE, "source": "TRACKED"}),
            ("segment", "s2", {"entryUid": "e2", "kind": "WORK", "startUtcMs": 500, "endUtcMs": None,
                               "zoneId": self.ZONE, "source": "TRACKED"}),
            ("absence", "a1", {"date": "2026-11-09", "kind": "FULL_DAY", "timeMin": None}),
            ("workSchedule", "w1", {"dayOfWeek": 1, "startMin": 540, "endMin": 1080}),
        ):
            pulled({"type": type_, "uid": uid, "rev": 3, "fields": fields})
        s.set_state("cursor", 77)
        # a store of the library before 1.0 has no model
        with s.transaction() as db:
            db.execute("DELETE FROM state WHERE key = 'model'")

    def reopen(self):
        self.store.close()
        old = os.environ.get("TZ")
        os.environ["TZ"] = self.ZONE
        self.addCleanup(lambda: os.environ.__setitem__("TZ", old) if old else os.environ.pop("TZ", None))
        self.store = Store(self.path)
        return self.store

    def test_a_fresh_store_has_the_model(self):
        fresh = Store(Path(self.dir.name) / "new.sqlite3")
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.get_state("model"), MODEL)

    def test_the_records_migrate_on_open(self):
        s = self.reopen()
        self.assertEqual(s.get_state("model"), MODEL)
        # a full realignment: from the beginning
        self.assertEqual(s.cursor, 0)
        self.assertEqual(s.all("todo"), [])
        self.assertEqual(s.all("absence"), [])
        e1, e2 = s.get("entry", "e1"), s.get("entry", "e2")
        # the open to-do with only closed entries stays its own TODO; the entry refers to it
        self.assertEqual((e1["status"], e1["kind"], e1["closedUtcMs"], e1["tags"]), ("CLOSED", "ACTIVITY", 400, ["x"]))
        self.assertEqual((e2["status"], e2["mode"], e2["closedUtcMs"]), ("RUNNING", "WORK", None))
        t1, t2 = s.get("entry", "t1"), s.get("entry", "t2")
        self.assertEqual((t1["status"], t1["activity"], t1["planDate"], t1["priority"], t1["pluginFields"]),
                         ("TODO", "Ticket", "2026-11-12", 4, {"a": 1}))
        self.assertEqual((t2["status"], t2["closedUtcMs"]), ("CLOSED", 900))
        refs = {(r["entryUid"], r["kind"], r["value"]) for r in s.all("externalRef")}
        self.assertEqual(refs, {("t1", "TICKET", "INC1"), ("e1", "UUID", "t1")})
        # the absence row became an activity with a segment on the Monday of the schedule
        (a,) = [e for e in s.all("entry") if e["kind"] == "ABSENCE"]
        self.assertEqual((a["status"], a["absenceKind"]), ("CLOSED", "ABSENCE"))
        (seg,) = [x for x in s.all("segment") if x["entryUid"] == a["uid"]]
        self.assertEqual((seg["kind"], seg["planned"], seg["source"]), ("ABSENCE", False, "MANUAL"))
        # the other records stay
        self.assertEqual(s.get("client", "c1")["name"], "Acme")
        self.assertEqual(len(s.all("workSchedule")), 1)
        # what is there is the server's view migrated, nothing to push
        self.assertEqual(s.pending_count(), 0)

    def test_it_runs_once(self):
        s = self.reopen()
        s.set_state("cursor", 5)
        before = sorted((r["uid"], json.dumps(r, sort_keys=True)) for r in s.all("entry"))
        s.close()
        s = Store(self.path)
        self.addCleanup(s.close)
        self.assertEqual(s.cursor, 5)
        self.assertEqual(sorted((r["uid"], json.dumps(r, sort_keys=True)) for r in s.all("entry")), before)

    def test_the_local_changes_not_yet_pushed_come_along(self):
        s = self.store
        # on an entry: a note, a stop with the old statuses; a new entry; a todo ticked off; a deleted entry
        s.write("entry", "e2", {"notes": "scritta", "status": "TO_COMPLETE", "mode": None}, 1000)
        s.write("segment", "s2", {"endUtcMs": 600}, 1000)
        s.write("entry", "new", {"status": "ACTIVE", "mode": "TRAVEL", "createdUtcMs": 700, "updatedUtcMs": 700,
                                 "zoneId": self.ZONE, "needsReview": False}, 1000)
        s.write("segment", "snew", {"entryUid": "new", "kind": "TRAVEL", "startUtcMs": 700, "zoneId": self.ZONE,
                                    "source": "TRACKED"}, 1000)
        s.write("todo", "t1", {"title": "Ticket 2"}, 1000)
        s.write("todo", "pending-todo", {"title": "Nuovo", "done": False, "createdUtcMs": 800}, 1000)
        s.delete("entry", "e1", 1000)
        s = self.reopen()
        e2 = s.get("entry", "e2")
        self.assertEqual((e2["status"], e2["notes"], e2["closedUtcMs"], e2["_pending"]),
                         ("CLOSED", "scritta", 600, True))
        new = s.get("entry", "new")
        self.assertEqual((new["status"], new["mode"], new["kind"]), ("RUNNING", "TRAVEL", "ACTIVITY"))
        self.assertEqual(s.get("entry", "t1")["activity"], "Ticket 2")
        self.assertEqual(s.get("entry", "pending-todo")["activity"], "Nuovo")
        self.assertIsNone(s.get("entry", "e1"))
        # what goes to the server is in the new shape; parents before children; no to-do or absence left
        pending = [(r["type"], r["uid"], r["op"]) for r in s.pending(100)]
        self.assertNotIn("todo", [p[0] for p in pending])
        self.assertIn(("entry", "e1", "delete"), pending)
        # within a type the deletes first: what they free (an external key) is there for the upserts
        self.assertLess(pending.index(("entry", "e1", "delete")), pending.index(("entry", "e2", "upsert")))
        self.assertLess(pending.index(("entry", "new", "upsert")), pending.index(("segment", "snew", "upsert")))
        change = {c["uid"]: c for c in map(s.change, s.pending(100)) if c["type"] == "entry" and c["op"] == "upsert"}
        self.assertEqual(change["e2"]["fields"]["status"], "CLOSED")
        self.assertEqual(change["e2"]["fields"]["notes"], "scritta")
        self.assertNotIn("activity", change["e2"]["fields"])
        self.assertEqual(change["new"]["fields"]["kind"], "ACTIVITY")
        self.assertEqual(set(change["e2"]["times"]), set(change["e2"]["fields"]))

    def test_the_realignment_pushes_the_local_changes_and_drops_what_the_server_lacks(self):
        s = self.store
        s.write("entry", "e2", {"notes": "scritta"}, 1000)
        s.write("todo", "t1", {"title": "Ticket 2"}, 1000)
        s = self.reopen()
        self.assertTrue(s.get_state("realign"))
        self.assertTrue(s.realigning())
        hour = 3_600_000
        later = int(time.time() * 1000) + hour
        clock = later + hour
        server = FakeServer(now=lambda: clock)
        # the server migrated its copy after this store did, stamping every field with its own time; t2 is
        # not there (on the server another activity absorbed it), nor anything derived from it
        for r in s.db.execute("SELECT type, uid, fields FROM record WHERE type IN ('entry', 'segment', 'externalRef')"):
            if r["uid"] != "t2":
                server.put(r["type"], r["uid"], json.loads(r["fields"]), at=later)
        server.put("entry", "e2", {"notes": "dal server"}, at=later)
        engine = SyncEngine(s, server, clock=lambda: clock)
        # a sync that fails leaves the realignment to do
        server.status = 503
        with self.assertRaises(SyncFailed):
            engine.sync()
        self.assertTrue(s.get_state("realign"))
        self.assertIsNotNone(s.get("entry", "t2"))
        server.status = None
        engine.sync()
        # the local changes won, with the time of the sync
        self.assertEqual(server.records[("entry", "e2")]["fields"]["notes"], "scritta")
        self.assertEqual(server.records[("entry", "e2")]["clock"]["notes"], clock)
        self.assertEqual(server.records[("entry", "t1")]["fields"]["activity"], "Ticket 2")
        self.assertEqual((s.get("entry", "e2")["notes"], s.get("entry", "t1")["activity"]), ("scritta", "Ticket 2"))
        # the server's version for the rest; no ghost of what it does not have
        self.assertIsNone(s.get("entry", "t2"))
        self.assertEqual(s.get("entry", "e1")["status"], "CLOSED")
        self.assertEqual(s.get("client", "c1")["name"], "Acme")
        self.assertEqual(s.pending_count(), 0)
        self.assertFalse(s.get_state("realign"))
        self.assertFalse(s.realigning())
        # later syncs are as usual: a change carries its own time
        s.write("entry", "e2", {"notes": "dopo"}, clock + 5)
        engine.sync()
        self.assertEqual(server.records[("entry", "e2")]["clock"]["notes"], clock + 5)

    def test_a_ghost_with_a_local_change_goes_and_the_user_is_told(self):
        s = self.reopen()
        server = FakeServer(now=lambda: 10)
        s.write("entry", "t2", {"notes": "mia"}, 5)
        with self.assertLogs("activitytrack.store", "WARNING"):
            result = SyncEngine(s, server, clock=lambda: 10).sync()
        # made by the migration, not on the server: a PC is no source of trust (issue #21), nothing pushed
        self.assertEqual([c for _, path, body in server.requests if path == "sync/push" for c in body["changes"]], [])
        self.assertIsNone(s.get("entry", "t2"))
        self.assertIsNone(s.get("entry", "e1"))
        self.assertEqual(s.pending_count(), 0)
        self.assertEqual([(n["code"], n["uid"], n["title"], n["fields"]) for n in result.notices],
                         [("edit_dropped", "t2", "Chiamare", {"notes": "mia"})])
        self.assertFalse(s.realigning())

    def test_old_changes_written_after_the_migration(self):
        s = self.reopen()
        # an older process sharing the folder: a to-do, an entry stopped with the old statuses
        s.write("todo", "old", {"title": "x"}, 2000)
        s.write("entry", "e2", {"status": "TO_COMPLETE", "todoUid": "t1", "notes": "n"}, 2000)
        with self.assertLogs("activitytrack.store", "WARNING"):
            dropped = s.legacy_pending()
        self.assertEqual(len(dropped), 1)
        self.assertIn("todo old", dropped[0])
        (change,) = [s.change(r) for r in s.pending(10)]
        self.assertEqual(change["fields"], {"status": "CLOSED", "closedUtcMs": 2000, "notes": "n"})
        self.assertEqual(set(change["times"]), set(change["fields"]))
        self.assertEqual(s.legacy_pending(), [])

    def test_the_zone_of_the_migration(self):
        def absence_start(store):
            (a,) = [e for e in store.all("entry") if e["kind"] == "ABSENCE"]
            (seg,) = [x for x in store.all("segment") if x["entryUid"] == a["uid"]]
            return seg["startUtcMs"]

        def nine(zone):
            return int(datetime(2026, 11, 9, 9, tzinfo=ZoneInfo(zone)).timestamp() * 1000)

        self.store.close()
        # the interface's zone
        s = Store(self.path, zone="America/New_York")
        self.addCleanup(s.close)
        self.assertEqual(absence_start(s), nine("America/New_York"))

    def test_without_a_zone_of_the_system_the_records_tell_it(self):
        self.store.close()
        # Windows: no IANA zone from the system; the entries were recorded in Rome
        with mock.patch("activitytrack.work.system_zone", return_value=None):
            s = Store(self.path)
        self.addCleanup(s.close)
        self.assertEqual(s.get_state("model"), MODEL)
        (a,) = [e for e in s.all("entry") if e["kind"] == "ABSENCE"]
        (seg,) = [x for x in s.all("segment") if x["entryUid"] == a["uid"]]
        nine = datetime(2026, 11, 9, 9, tzinfo=ZoneInfo(self.ZONE))
        self.assertEqual(seg["startUtcMs"], int(nine.timestamp() * 1000))

    def test_reset_keeps_the_model(self):
        self.reopen().reset()
        self.assertEqual(self.store.get_state("model"), MODEL)
        # records of schema 3 arrive; a new open does not migrate them
        self.store.pulled({"type": "entry", "uid": "n", "rev": 1, "fields": {"status": "TODO", "kind": "ACTIVITY"}})
        self.store.close()
        s = Store(self.path)
        self.addCleanup(s.close)
        self.assertEqual(s.get("entry", "n")["status"], "TODO")
