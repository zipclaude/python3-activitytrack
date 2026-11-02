# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
import unittest
from pathlib import Path

from activitytrack.policy import Policy
from activitytrack.store import Store
from activitytrack.work import (
    ACTIVE, CLOSED, PAUSE, REOPEN, STOP, SUSPEND, SUSPENDED, TO_COMPLETE, TRAVEL, WORK, StartRequest, Work, WorkError,
    ref_kind, transition,
)

MIN = 60_000


def policy(**sections):
    return Policy({"format": "activitytrack-policy", "schema": 1, **sections})


class WorkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = 1_000 * MIN
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        s = self.store
        s.pulled({"type": "company", "uid": "acme", "rev": 1, "central": True,
                  "fields": {"name": "Acme", "archived": False}})
        s.pulled({"type": "client", "uid": "lib", "rev": 2, "central": True,
                  "fields": {"name": "Biblioteca", "companyUid": "acme", "archived": False}})
        s.pulled({"type": "tag", "uid": "t1", "rev": 3, "fields": {"name": "Urgente", "archived": False}})

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def start(self, **kw):
        kw.setdefault("client", "lib")
        kw.setdefault("activity", "Rete")
        return self.work.start(StartRequest(**kw))

    def test_the_state_machine(self):
        self.assertEqual(transition(ACTIVE, WORK, PAUSE), (ACTIVE, PAUSE, True, PAUSE))
        self.assertEqual(transition(SUSPENDED, None, WORK), (ACTIVE, WORK, False, WORK))
        self.assertEqual(transition(SUSPENDED, None, STOP), (TO_COMPLETE, None, False, None))
        self.assertEqual(transition(CLOSED, None, REOPEN), (SUSPENDED, None, False, None))
        for state, cmd in ((ACTIVE, WORK), (SUSPENDED, PAUSE), (CLOSED, TRAVEL)):
            with self.assertRaises(WorkError):
                transition(state, WORK if state == ACTIVE else None, cmd)
        with self.assertRaises(WorkError):
            transition(ACTIVE, PAUSE, SUSPEND)

    def test_resume_suspends_the_others_without_concurrent_entries(self):
        a = self.start()
        self.now += 10 * MIN
        self.work.command(a, SUSPEND)
        b = self.start()
        self.now += 10 * MIN
        self.work.policy = policy(features={"concurrentEntries": False})
        self.work.command(a, WORK)
        self.assertEqual((self.store.get("entry", a)["status"], self.store.get("entry", b)["status"]), (ACTIVE, SUSPENDED))
        self.work.command(a, PAUSE)
        self.now += 10 * MIN
        self.work.command(b, TRAVEL)
        self.assertEqual(self.store.get("entry", b)["status"], ACTIVE)
        self.assertIn(self.store.get("entry", a)["status"], (TO_COMPLETE, CLOSED))

    def test_start_pause_stop(self):
        uid = self.start()
        e = self.store.get("entry", uid)
        self.assertEqual((e["status"], e["mode"], e["companyUid"], e["zoneId"]), (ACTIVE, WORK, "acme", "Europe/Rome"))
        self.now += 30 * MIN
        self.work.command(uid, PAUSE)
        self.now += 10 * MIN
        self.work.command(uid, WORK)
        self.now += 20 * MIN
        # client and description: closed at once
        self.assertEqual(self.work.command(uid, STOP), CLOSED)
        segs = self.work.segments(uid)
        self.assertEqual([s["kind"] for s in segs], [WORK, PAUSE, WORK])
        self.assertTrue(all(s["endUtcMs"] for s in segs))
        self.assertEqual(self.work.totals(uid), {WORK: 50 * MIN, TRAVEL: 0, PAUSE: 10 * MIN})
        # everything is a pending change for the sync, entry before its segments
        types = [r["type"] for r in self.store.pending(100)]
        self.assertEqual(types[0], "entry")

    def test_stop_without_description_or_from_a_todo_asks_details(self):
        a = self.start(activity="")
        self.assertEqual(self.work.command(a, STOP), TO_COMPLETE)
        with self.assertRaises(WorkError) as e:
            self.work.complete(a)
        self.assertEqual(e.exception.code, "details_missing")
        self.work.edit(a, activity="Cablaggio")
        self.work.complete(a)
        self.assertEqual(self.store.get("entry", a)["status"], CLOSED)
        self.store.pulled({"type": "todo", "uid": "td", "rev": 4,
                           "fields": {"title": "Ticket", "refs": "INC0012345\nhttps://x.example/1", "done": False}})
        b = self.start(todo="td")
        refs = sorted((r["kind"], r["value"]) for r in self.store.all("externalRef") if r["entryUid"] == b)
        self.assertEqual(refs, [("TICKET", "INC0012345"), ("URL", "https://x.example/1")])
        self.assertEqual(self.work.command(b, STOP), TO_COMPLETE)
        self.assertTrue(self.work.close_if_complete(b))

    def test_close_complete_on_stop_off(self):
        self.work.policy = policy(behaviours={"closeCompleteOnStop": False})
        self.assertEqual(self.work.command(self.start(), STOP), TO_COMPLETE)

    def test_a_client_is_needed_except_for_a_travel(self):
        with self.assertRaises(WorkError) as e:
            self.work.start(StartRequest(activity="x"))
        self.assertEqual(e.exception.code, "client_required")
        uid = self.work.start(StartRequest(mode=TRAVEL))
        self.assertEqual(self.store.get("entry", uid)["mode"], TRAVEL)
        with self.assertRaises(WorkError):
            self.work.start(StartRequest(client="lib", mode=PAUSE))

    def test_free_text_master_data(self):
        uid = self.start(client="biblioteca")
        self.assertEqual(self.store.get("entry", uid)["clientUid"], "lib")
        uid = self.start(company="Acme", client="Comune di Valdora", role="Sistemista")
        e = self.store.get("entry", uid)
        client = self.store.get("client", e["clientUid"])
        self.assertEqual((client["name"], client["companyUid"], client["_central"]),
                         ("Comune di Valdora", "acme", False))
        self.assertEqual(self.store.get("role", e["roleUid"])["name"], "Sistemista")
        self.work.policy = policy(masterData={"source": "importOnly"})
        pending = len(self.store.pending(1000))
        with self.assertRaises(WorkError) as err:
            self.start(client="Nuovo cliente")
        self.assertEqual(err.exception.code, "master_locked")
        # refused: nothing written
        self.assertEqual(len(self.store.pending(1000)), pending)

    def test_tags(self):
        uid = self.start(tags=["urgente", "Nuovo", "Nuovo"])
        self.assertEqual(self.store.get("entry", uid)["tags"], ["Urgente", "Nuovo"])
        self.work.policy = policy(settings={"tagsFreeText": {"value": False, "locked": True}})
        with self.assertRaises(WorkError):
            self.start(tags=["Altro"])

    def test_another_start_and_the_running_ones(self):
        a = self.start()
        self.now += MIN
        b = self.start(others={a: SUSPEND})
        self.assertEqual(self.store.get("entry", a)["status"], SUSPENDED)
        self.assertEqual([e["uid"] for e in self.work.running()], [b, a])
        # without concurrent activities the running one is suspended anyway
        self.work.policy = policy(features={"concurrentEntries": False})
        self.now += MIN
        c = self.start()
        self.assertEqual(self.store.get("entry", b)["status"], SUSPENDED)
        self.assertEqual(self.store.get("entry", c)["status"], ACTIVE)

    def test_past_activities(self):
        uid = self.start(start_ms=self.now - 90 * MIN, end_ms=self.now - 30 * MIN)
        e = self.store.get("entry", uid)
        self.assertEqual(e["status"], CLOSED)
        self.assertEqual(self.work.totals(uid)[WORK], 60 * MIN)
        self.assertEqual(self.work.segments(uid)[0]["source"], "MANUAL")
        for kw, code in ((dict(start_ms=self.now + MIN), "future_time"),
                         (dict(start_ms=self.now - MIN, end_ms=self.now - 2 * MIN), "invalid_range"),
                         (dict(start_ms=self.now - MIN, end_ms=self.now - MIN + 10_000), "too_short")):
            with self.assertRaises(WorkError) as err:
                self.start(**kw)
            self.assertEqual(err.exception.code, code)

    def test_a_backdated_command_is_clamped(self):
        uid = self.start()
        self.now += 30 * MIN
        self.work.command(uid, PAUSE, at=self.now - 100 * MIN)
        pause = self.work.open_segment(uid)
        self.assertEqual(pause["startUtcMs"], self.now - 30 * MIN)
        with self.assertRaises(WorkError):
            self.work.command(uid, WORK, at=self.now - 40 * MIN, exact=True)

    def test_times_and_the_review_mark(self):
        uid = self.start()
        self.now += 60 * MIN
        self.work.command(uid, STOP)
        seg = self.work.segments(uid)[0]
        self.work.add_segment(uid, self.now - 30 * MIN, self.now - 20 * MIN, TRAVEL)
        self.assertTrue(self.store.get("entry", uid)["needsReview"])
        self.work.edit_segment(seg["uid"], seg["startUtcMs"], self.now - 30 * MIN, WORK)
        self.assertFalse(self.store.get("entry", uid)["needsReview"])
        self.work.policy = policy(settings={"editableTimes": {"value": False, "locked": True}})
        with self.assertRaises(WorkError) as err:
            self.work.remove_segment(seg["uid"])
        self.assertEqual(err.exception.code, "times_locked")

    def test_required_fields_and_features(self):
        self.work.policy = policy(fields={"role": {"required": True}, "client": {"visible": False}})
        with self.assertRaises(WorkError) as err:
            self.start()
        self.assertEqual((err.exception.code, err.exception.detail), ("required", "role"))
        # essential fields keep their rules
        self.assertTrue(self.work.policy.field("client").visible)
        self.work.policy = policy(features={"travel": False}, entries={"source": "todosOnly"})
        with self.assertRaises(WorkError) as err:
            self.start()
        self.assertEqual(err.exception.code, "todos_only")

    def test_ref_kinds(self):
        self.assertEqual([ref_kind(v) for v in ("https://j.example/OPS-1", "OPS-42", "#12", "PO 2026/Q4")],
                         ["URL", "TICKET", "TICKET", "OTHER"])


class PolicyTest(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(Policy.parse("{"))
        self.assertIsNone(Policy.parse(json.dumps({"format": "x"})))
        self.assertTrue(Policy.parse(None).feature("travel"))
        p = Policy.parse(json.dumps({
            "format": "activitytrack-policy", "schema": 1, "features": {"travel": False},
            "fields": {"client": {"label": {"it": "Cantiere", "en": "Site"}},
                       "km": {"visible": False, "required": True}},
            "settings": {"editableTimes": {"value": False, "locked": True}, "confirmIntervalMin": 30},
            # a phone-only key does not make it refuse the policy
            "integrations": {"antares": {"plugin": "antares.json"}},
        }))
        self.assertFalse(p.feature("travel"))
        self.assertEqual(p.field("client").label, "Cantiere")
        self.assertEqual(p.field("client", "de").label, "Site")
        self.assertFalse(p.field("km").required)
        self.assertFalse(p.setting("editableTimes", True))
        self.assertTrue(p.setting_fixed("editableTimes"))
        self.assertEqual(p.setting("confirmIntervalMin", 60), 30)
        self.assertEqual(p.setting("confirmIntervalMin", True), True)
