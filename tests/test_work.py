# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from activitytrack import model
from activitytrack.policy import Policy
from activitytrack.store import Store
from activitytrack.work import (
    ABSENCE, CLOSED, PAUSE, RUNNING, STOP, SUSPEND, TICK, TODO, TRAVEL, WORK, StartRequest, Work,
    WorkError, ref_kind, transition,
)

MIN = 60_000
ROME = ZoneInfo("Europe/Rome")


def policy(**sections):
    return Policy({"format": "activitytrack-policy", "schema": 1, **sections})


def at(h, m=0, s=0, day=11):
    return int(datetime(2026, 11, day, h, m, s, tzinfo=ROME).timestamp() * 1000)


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

    def entry(self, uid):
        return self.store.get("entry", uid)

    def status(self, uid):
        return self.entry(uid)["status"]

    def todo(self, **kw):
        return self.work.save_todo(None, kw.pop("activity", "Da fare"), **kw)

    def kinds(self, uid, planned=False):
        return [(s["kind"], s["startUtcMs"], s.get("endUtcMs")) for s in self.work.segments(uid)
                if model.is_planned(s) == planned]

    def test_the_state_machine(self):
        self.assertEqual(transition(TODO, None, WORK), (RUNNING, WORK, False, WORK))
        self.assertEqual(transition(TODO, None, TICK), (CLOSED, None, False, None))
        self.assertEqual(transition(RUNNING, WORK, PAUSE), (RUNNING, PAUSE, True, PAUSE))
        self.assertEqual(transition(RUNNING, PAUSE, TRAVEL), (RUNNING, TRAVEL, True, TRAVEL))
        self.assertEqual(transition(RUNNING, WORK, SUSPEND), (RUNNING, None, True, None))
        self.assertEqual(transition(RUNNING, None, WORK), (RUNNING, WORK, False, WORK))
        self.assertEqual(transition(RUNNING, None, STOP), (CLOSED, None, False, None))
        self.assertEqual(transition(RUNNING, TRAVEL, STOP), (CLOSED, None, True, None))
        self.assertEqual(transition(CLOSED, None, TRAVEL), (RUNNING, TRAVEL, False, TRAVEL))
        for state, mode, cmd in ((RUNNING, WORK, WORK), (RUNNING, None, PAUSE), (RUNNING, None, SUSPEND),
                                 (CLOSED, None, PAUSE), (CLOSED, None, STOP), (TODO, None, PAUSE),
                                 (TODO, None, STOP), (RUNNING, WORK, TICK), (CLOSED, None, TICK)):
            with self.assertRaises(WorkError, msg=(state, mode, cmd)):
                transition(state, mode, cmd)

    def test_busy_minutes_of_a_day(self):
        rome = ZoneInfo("Europe/Rome")
        day_at = lambda h, m=0: int(datetime(2026, 11, 2, h, m, tzinfo=rome).timestamp() * 1000)  # noqa: E731
        self.now = day_at(16)
        self.start(start_ms=day_at(9), end_ms=day_at(10, 30))
        running = self.start(start_ms=day_at(15))
        seg = self.work.open_segment(running)
        # a planned segment is not time taken
        todo = self.todo()
        self.work.add_segment(todo, day_at(11), day_at(12), WORK, planned=True)
        self.assertEqual(self.work.busy(datetime(2026, 11, 2).date()), [(540, 630), (900, 960)])
        self.assertEqual(self.work.busy(datetime(2026, 11, 2).date(), skip=seg["uid"]), [(540, 630)])

    def test_start_pause_stop(self):
        uid = self.start()
        e = self.entry(uid)
        self.assertEqual((e["kind"], e["status"], e["mode"], e["companyUid"], e["zoneId"], e["priority"]),
                         (model.ACTIVITY, RUNNING, WORK, "acme", "Europe/Rome", 0))
        self.now += 30 * MIN
        self.work.switch(uid, PAUSE)
        self.assertEqual(self.work.open_segment(uid)["kind"], PAUSE)
        self.now += 10 * MIN
        self.work.switch(uid, WORK)
        self.now += 20 * MIN
        self.assertEqual(self.work.stop(uid), CLOSED)
        e = self.entry(uid)
        self.assertEqual((e["status"], e["mode"], e["closedUtcMs"]), (CLOSED, None, self.now))
        segs = self.work.segments(uid)
        self.assertEqual([s["kind"] for s in segs], [WORK, PAUSE, WORK])
        self.assertTrue(all(s["endUtcMs"] for s in segs))
        self.assertEqual(self.work.totals(uid), {WORK: 50 * MIN, TRAVEL: 0, PAUSE: 10 * MIN, ABSENCE: 0})
        # everything is a pending change for the sync, entry before its segments
        types = [r["type"] for r in self.store.pending(100)]
        self.assertEqual(types[0], "entry")

    def test_displayed_states_follow_the_commands(self):
        uid = self.start()
        self.assertEqual(self.work.display(self.entry(uid))[0], "WORK")
        self.now += 10 * MIN
        self.work.suspend(uid)
        self.assertEqual(self.work.display(self.entry(uid))[0], "SUSPENDED")
        self.work.resume(uid, TRAVEL)
        self.assertEqual(self.work.display(self.entry(uid))[0], "TRAVEL")
        self.now += MIN
        self.work.switch(uid, PAUSE)
        self.assertEqual(self.work.display(self.entry(uid))[0], "PAUSE")
        self.assertEqual(self.work.resume_kind(uid), TRAVEL)
        self.now += MIN
        self.work.stop(uid)
        # a client and a WORK segment
        self.assertEqual(self.work.display(self.entry(uid))[0], "DONE")
        self.work.edit(uid, client="")
        self.assertEqual(self.work.display(self.entry(uid))[0], "INCOMPLETE")
        # travel only
        trip = self.start(mode=TRAVEL)
        self.now += MIN
        self.work.stop(trip)
        self.assertEqual(self.work.display(self.entry(trip))[0], "INCOMPLETE")
        self.work.reopen(trip, WORK)
        self.assertEqual(self.work.display(self.entry(trip))[0], "WORK")

    def test_commands_refused_by_the_state(self):
        uid = self.start()
        for call in (lambda: self.work.resume(uid), lambda: self.work.switch(uid, WORK),
                     lambda: self.work.reopen(uid), lambda: self.work.tick(uid),
                     lambda: self.work.start_todo(uid), lambda: self.work.switch(uid, TODO)):
            with self.assertRaises(WorkError):
                call()
        self.now += MIN
        self.work.suspend(uid)
        with self.assertRaises(WorkError):
            self.work.suspend(uid)
        with self.assertRaises(WorkError):
            self.work.switch(uid, PAUSE)
        self.work.stop(uid)
        with self.assertRaises(WorkError):
            self.work.stop(uid)

    def test_a_todo_starts_ticks_and_is_deleted(self):
        t = self.todo(activity="Ticket", client="lib", plan_date=date(2026, 11, 12), priority=3)
        e = self.entry(t)
        self.assertEqual((e["kind"], e["status"], e["mode"], e["priority"], e["planDate"]),
                         (model.ACTIVITY, TODO, None, 3, "2026-11-12"))
        self.work.add_segment(t, self.now + 60 * MIN, self.now + 120 * MIN, WORK, planned=True)
        self.assertEqual(self.work.totals(t)[WORK], 0)
        self.assertEqual(self.work.display(e)[0], "PLANNED")
        # the plan has served: the planned segment goes, the real one opens
        self.assertEqual(self.work.start_todo(t), t)
        self.assertEqual(self.kinds(t, planned=True), [])
        self.assertEqual(self.kinds(t), [(WORK, self.now, None)])
        self.assertEqual((self.status(t), self.entry(t)["mode"]), (RUNNING, WORK))
        # a task ticked off, and taken back
        u = self.todo(activity="Chiamare")
        self.now += MIN
        self.work.tick(u)
        self.assertEqual((self.status(u), self.entry(u)["closedUtcMs"]), (CLOSED, self.now))
        self.assertEqual(self.work.display(self.entry(u))[0], "DONE")
        self.work.untick(u)
        self.assertEqual((self.status(u), self.entry(u)["closedUtcMs"]), (TODO, None))
        with self.assertRaises(WorkError):
            self.work.untick(t)
        # the bin
        self.work.delete(u)
        self.assertEqual(self.work.display(self.entry(u)), ("DELETED", datetime.fromtimestamp(self.now / 1000, ROME)))
        self.assertNotIn(u, [e["uid"] for e in self.work.entries()])
        self.assertEqual([e["uid"] for e in self.work.entries(deleted=True)], [u])
        with self.assertRaises(WorkError):
            self.work.tick(u)
        self.work.restore(u)
        self.assertIn(u, [e["uid"] for e in self.work.todo_list()])
        with self.assertRaises(WorkError):
            self.work.restore(u)
        self.work.policy = policy(features={"deleteTodos": False, "ignoreTodos": False})
        with self.assertRaises(WorkError):
            self.work.delete(u)

    def test_start_a_todo_by_a_request_keeps_its_details(self):
        t = self.todo(activity="Ticket", client="lib", notes="n")
        uid = self.work.start(StartRequest(todo=t, notes="altre", mode=TRAVEL))
        self.assertEqual(uid, t)
        e = self.entry(t)
        self.assertEqual((e["activity"], e["clientUid"], e["notes"], e["mode"]), ("Ticket", "lib", "altre", TRAVEL))
        # no client, nothing to start from
        bare = self.todo()
        with self.assertRaises(WorkError) as err:
            self.work.start(StartRequest(todo=bare))
        self.assertEqual(err.exception.code, "client_required")
        self.work.start_todo(bare)
        self.assertEqual(self.status(bare), RUNNING)

    def test_todo_validation(self):
        for kw, code in ((dict(activity=" "), "title_required"), (dict(plan_min=60), "plan_day_required"),
                         (dict(plan_date=date(2026, 11, 12), duration_min=30), "plan_time_required"),
                         (dict(plan_date=date(2026, 11, 12), plan_min=1500), "invalid_time"),
                         (dict(priority=6), "invalid_priority"), (dict(priority=-1), "invalid_priority")):
            with self.assertRaises(WorkError) as err:
                self.todo(**kw)
            self.assertEqual(err.exception.code, code)
        t = self.todo(plan_date=date(2026, 11, 12), plan_min=600, duration_min=30)
        self.work.save_todo(t, "Nuovo titolo", due_date=date(2026, 11, 20), priority=5)
        e = self.entry(t)
        self.assertEqual((e["activity"], e["dueDate"], e["priority"], e["planDate"]),
                         ("Nuovo titolo", "2026-11-20", 5, None))
        self.work.policy = policy(features={"planning": False})
        with self.assertRaises(WorkError):
            self.todo(plan_date=date(2026, 11, 12))
        self.work.policy = policy(fields={"todoPriority": {"required": True}})
        with self.assertRaises(WorkError) as err:
            self.todo()
        self.assertEqual(err.exception.detail, "todoPriority")
        self.work.start_todo(t)
        with self.assertRaises(WorkError):
            self.work.save_todo(t, "x", priority=1)

    def test_planned_segments(self):
        t = self.todo()
        a = self.work.add_segment
        a(t, self.now + 60 * MIN, self.now + 120 * MIN, WORK, planned=True)
        with self.assertRaises(WorkError):
            a(t, self.now + 60 * MIN, self.now + 120 * MIN, ABSENCE, planned=True)
        with self.assertRaises(WorkError):
            a(t, self.now + 60 * MIN, self.now + 120 * MIN, ABSENCE)
        # a real one may not lie ahead
        with self.assertRaises(WorkError) as err:
            a(t, self.now + 60 * MIN, self.now + 120 * MIN, WORK)
        self.assertEqual(err.exception.code, "future_time")
        done = self.start()
        with self.assertRaises(WorkError):
            a(done, self.now - 20 * MIN, self.now - 10 * MIN, WORK, planned=True)
        # replaced as a whole by save_todo
        self.work.save_todo(t, "Da fare", planned=[(self.now + 180 * MIN, self.now + 240 * MIN, WORK),
                                                   (self.now + 300 * MIN, self.now + 360 * MIN, TRAVEL)])
        self.assertEqual(len(self.kinds(t, planned=True)), 2)
        self.work.tick(t)
        self.assertEqual(self.kinds(t, planned=True), [])

    def test_stop_without_client_or_work_is_displayed_incomplete(self):
        a = self.start(activity="")
        self.assertEqual(self.work.stop(a), CLOSED)
        self.assertEqual(self.work.display(self.entry(a))[0], "DONE")
        self.work.edit(a, client="")
        self.assertEqual(self.work.display(self.entry(a))[0], "INCOMPLETE")
        self.work.edit(a, client="lib")
        self.assertEqual(self.work.display(self.entry(a))[0], "DONE")
        b = self.start(todo=self.todo(client="lib"))
        self.assertEqual(self.status(b), RUNNING)

    def test_a_client_is_needed_except_for_a_travel(self):
        with self.assertRaises(WorkError) as e:
            self.work.start(StartRequest(activity="x"))
        self.assertEqual(e.exception.code, "client_required")
        uid = self.work.start(StartRequest(mode=TRAVEL))
        self.assertEqual(self.entry(uid)["mode"], TRAVEL)
        with self.assertRaises(WorkError):
            self.work.start(StartRequest(client="lib", mode=PAUSE))

    def test_free_text_master_data(self):
        uid = self.start(client="biblioteca")
        self.assertEqual(self.entry(uid)["clientUid"], "lib")
        uid = self.start(company="Acme", client="Comune di Valdora", role="Sistemista")
        e = self.entry(uid)
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
        self.assertEqual(self.entry(uid)["tags"], ["Urgente", "Nuovo"])
        self.work.policy = policy(settings={"tagsFreeText": {"value": False, "locked": True}})
        with self.assertRaises(WorkError):
            self.start(tags=["Altro"])

    def test_another_start_and_the_running_ones(self):
        a = self.start()
        self.now += MIN
        b = self.start(others={a: SUSPEND})
        self.assertIsNone(self.work.open_segment(a))
        self.assertEqual((self.status(a), self.entry(a)["mode"]), (RUNNING, None))
        self.assertEqual([e["uid"] for e in self.work.running()], [b, a])
        # without concurrent activities the running one is suspended anyway
        self.work.policy = policy(features={"concurrentEntries": False})
        self.now += MIN
        c = self.start()
        self.assertIsNone(self.work.open_segment(b))
        self.assertEqual(self.entry(c)["mode"], WORK)

    def test_past_activities(self):
        uid = self.start(start_ms=self.now - 90 * MIN, end_ms=self.now - 30 * MIN)
        e = self.entry(uid)
        self.assertEqual(e["status"], CLOSED)
        self.assertEqual(self.work.totals(uid)[WORK], 60 * MIN)
        self.assertEqual(self.work.segments(uid)[0]["source"], "MANUAL")
        for kw, code in ((dict(start_ms=self.now + MIN), "future_time"),
                         (dict(start_ms=self.now - MIN, end_ms=self.now - 2 * MIN), "invalid_range"),
                         (dict(start_ms=self.now - MIN, end_ms=self.now - MIN + 10_000), "too_short")):
            with self.assertRaises(WorkError) as err:
                self.start(**kw)
            self.assertEqual(err.exception.code, code)

    def test_a_past_activity_leaves_the_running_ones_alone(self):
        self.work.policy = policy(features={"concurrentEntries": False})
        a = self.start(start_ms=self.now - 60 * MIN)
        self.start(start_ms=self.now - 180 * MIN, end_ms=self.now - 120 * MIN)
        self.assertEqual(self.work.open_segment(a)["startUtcMs"], self.now - 60 * MIN)
        self.assertEqual(self.entry(a)["mode"], WORK)
        self.now += 10 * MIN
        self.assertEqual(self.work.totals(a)[WORK], 70 * MIN)

    def test_a_past_to_do_leaves_the_running_ones_alone(self):
        self.work.policy = policy(features={"concurrentEntries": False})
        a = self.start(start_ms=self.now - 60 * MIN)
        self.start(todo=self.todo(client="lib"), start_ms=self.now - 180 * MIN, end_ms=self.now - 120 * MIN)
        self.assertEqual(self.work.open_segment(a)["startUtcMs"], self.now - 60 * MIN)
        self.assertEqual(self.entry(a)["mode"], WORK)

    def test_a_backdated_command_is_clamped(self):
        uid = self.start()
        self.now += 30 * MIN
        self.work.switch(uid, PAUSE, at=self.now - 100 * MIN)
        pause = self.work.open_segment(uid)
        self.assertEqual(pause["startUtcMs"], self.now - 30 * MIN)
        with self.assertRaises(WorkError):
            self.work.switch(uid, WORK, at=self.now - 40 * MIN, exact=True)

    def test_times_and_the_review_mark(self):
        uid = self.start()
        self.now += 60 * MIN
        self.work.stop(uid)
        seg = self.work.segments(uid)[0]
        self.work.add_segment(uid, self.now - 30 * MIN, self.now - 20 * MIN, TRAVEL)
        self.assertTrue(self.entry(uid)["needsReview"])
        self.work.edit_segment(seg["uid"], seg["startUtcMs"], self.now - 30 * MIN, WORK)
        self.assertFalse(self.entry(uid)["needsReview"])
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
        for source in ("plannedOnly", "todosOnly"):
            self.work.policy = policy(features={"travel": False}, entries={"source": source})
            with self.assertRaises(WorkError) as err:
                self.start()
            self.assertEqual(err.exception.code, "planned_only")
        # from a TODO activity it goes
        self.work.start(StartRequest(todo=self.todo(client="lib")))

    def test_ref_kinds(self):
        self.assertEqual([ref_kind(v) for v in ("https://j.example/OPS-1", "OPS-42", "#12", "PO 2026/Q4")],
                         ["URL", "TICKET", "TICKET", "OTHER"])

    def test_resume_suspends_the_others_without_concurrent_entries(self):
        a = self.start()
        self.now += 10 * MIN
        self.work.suspend(a)
        b = self.start()
        self.now += 10 * MIN
        self.work.policy = policy(features={"concurrentEntries": False})
        self.work.resume(a, WORK)
        self.assertEqual((self.work.open_segment(a) is not None, self.work.open_segment(b) is not None),
                         (True, False))
        self.work.switch(a, PAUSE)
        self.now += 10 * MIN
        self.work.resume(b, TRAVEL)
        self.assertEqual(self.entry(b)["mode"], TRAVEL)
        # a paused activity is suspended, not stopped, like a working one
        self.assertEqual((self.status(a), self.entry(a)["mode"]), (RUNNING, None))
        self.assertEqual(self.work.display(self.entry(a))[0], "SUSPENDED")

    def test_a_new_activity_suspends_a_paused_one(self):
        self.work.policy = policy(features={"concurrentEntries": False})
        a = self.start()
        self.now += 10 * MIN
        self.work.switch(a, PAUSE)
        self.now += 5 * MIN
        b = self.start()
        self.assertEqual((self.status(a), self.entry(a)["mode"]), (RUNNING, None))
        self.assertEqual(self.work.display(self.entry(a))[0], "SUSPENDED")
        self.assertEqual(self.work.segments(a)[-1]["endUtcMs"], self.now)
        self.assertEqual(self.entry(b)["mode"], WORK)

    def test_perform_names_the_commands(self):
        t = self.todo(client="lib")
        self.work.perform(t, "start", WORK)
        self.now += MIN
        self.work.perform(t, "switch", PAUSE)
        self.now += MIN
        self.work.perform(t, "stop")
        self.now += MIN
        self.work.perform(t, "reopen", WORK)
        self.assertEqual((self.status(t), self.entry(t)["mode"]), (RUNNING, WORK))
        with self.assertRaises(WorkError):
            self.work.perform(t, "NOPE")


class ActionsTest(unittest.TestCase):
    """texts.entry_actions: the buttons a state allows (§3, §8), never "Sospendi"."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = at(10, 8)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        self.store.write("client", "lib", {"name": "L", "archived": False}, 1)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def verbs(self, uid):
        from activitytrack.texts import entry_actions
        return [(a.verb, a.kind) for a in entry_actions(self.work, self.work.entry(uid))]

    def test_by_state(self):
        t = self.work.save_todo(None, "x", client="lib")
        self.assertEqual(self.verbs(t), [("start", WORK), ("start", TRAVEL), ("tick", None), ("delete", None)])
        self.work.start(StartRequest(todo=t))
        self.assertEqual(self.verbs(t), [("switch", PAUSE), ("travel_from_work", TRAVEL), ("stop", None),
                                         ("delete", None)])
        self.now += MIN
        self.work.switch(t, PAUSE)
        self.assertEqual(self.verbs(t)[:3], [("switch", WORK), ("switch", TRAVEL), ("stop", None)])
        self.now += MIN
        self.work.suspend(t)
        self.assertEqual(self.verbs(t)[:3], [("resume", WORK), ("resume", TRAVEL), ("stop", None)])
        self.work.stop(t)
        self.assertEqual(self.verbs(t), [("reopen", WORK), ("delete", None)])
        self.work.delete(t)
        self.assertEqual(self.verbs(t), [("restore", None)])
        self.assertNotIn("suspend", [v for v, _k in self.verbs(t)])

    def test_a_ticked_task_goes_back_to_todo(self):
        t = self.work.save_todo(None, "x")
        self.work.tick(t)
        self.assertEqual(self.verbs(t)[0], ("untick", None))


class RoundingTest(unittest.TestCase):
    """ACTIVITY-MODEL.md 4.2: the action buttons store rounded instants when set."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = at(10, 7, 30)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome", round_stored=True)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def start(self, **kw):
        kw.setdefault("client", "c")
        self.store.write("client", "c", {"name": "C", "archived": False}, 1)
        return self.work.start(StartRequest(**kw))

    def segs(self, uid):
        return [(s["kind"], s["startUtcMs"], s.get("endUtcMs")) for s in self.work.segments(uid)]

    def test_off_by_default_and_only_with_editable_times(self):
        self.work.round_stored = None
        self.assertEqual(self.segs(self.start()), [(WORK, self.now, None)])
        self.work.round_stored = True
        self.work.policy = policy(settings={"editableTimes": {"value": False, "locked": True}})
        self.assertEqual(self.segs(self.start()), [(WORK, self.now, None)])
        # the policy can switch it on
        self.work.round_stored = None
        self.work.policy = policy(settings={"roundStoredTimes": {"value": True}})
        self.assertEqual(self.segs(self.start()), [(WORK, at(10), None)])

    def test_a_start_rounds_down_a_stop_up(self):
        uid = self.start()
        self.assertEqual(self.segs(uid), [(WORK, at(10), None)])
        self.now = at(11, 31)
        self.work.stop(uid)
        self.assertEqual(self.segs(uid), [(WORK, at(10), at(11, 45))])
        # the stop may lie ahead: shown closed at once
        e = self.store.get("entry", uid)
        self.assertEqual((e["status"], e["closedUtcMs"]), (CLOSED, self.now))
        self.assertEqual(self.work.display(e)[0], "INCOMPLETE" if not e.get("clientUid") else "DONE")
        # typed instants are kept as typed
        other = self.start(start_ms=at(9, 3))
        self.assertEqual(self.segs(other)[0][1], at(9, 3))
        self.work.stop(other, at=at(11, 33))
        self.assertEqual(self.segs(other)[0][2], at(11, 33))

    def test_a_reopen_starts_not_before_the_end_of_the_previous_segment(self):
        uid = self.start()
        self.now = at(10, 50)
        self.work.stop(uid)
        self.assertEqual(self.segs(uid)[0][2], at(11))
        # the stop rounded up to a time ahead: the next start is not before it
        self.now = at(10, 52)
        self.work.reopen(uid, WORK)
        # rounded down to 10:45, but that end ahead (11:00) was nominal: it moves back, no overlap
        self.assertEqual(self.segs(uid), [(WORK, at(10), at(10, 45)), (WORK, at(10, 45), None)])
        self.assertEqual(self.work.notices, [("end_moved", uid)])
        # an end that is past is a limit: the start is not before it
        self.work.stop(uid, at=at(10, 50))
        self.now = at(10, 52)
        self.work.reopen(uid, WORK)
        self.assertEqual(self.segs(uid)[-1][1], at(10, 50))

    def test_a_switch_uses_one_instant_the_nearest_of_the_smaller_quantum(self):
        uid = self.start(start_ms=at(9, 40))
        self.now = at(10, 7, 30)
        self.work.switch(uid, PAUSE)
        # work 15 and pause 5: the nearest multiple of 5, half up
        self.assertEqual(self.segs(uid), [(WORK, at(9, 40), at(10, 10)), (PAUSE, at(10, 10), None)])
        # the nearest is the pause's start: the exact instant, no segment of zero length (#179 R3)
        self.now = at(10, 12, 29)
        self.work.switch(uid, WORK)
        self.assertEqual(self.segs(uid)[1:], [(PAUSE, at(10, 10), at(10, 12, 29)), (WORK, at(10, 12, 29), None)])
        # never before the start of the segment being closed: 10:00 is, so the exact instant again
        self.work.quanta.update({WORK: 60, TRAVEL: 60})
        self.now = at(10, 14)
        self.work.switch(uid, TRAVEL)
        self.assertEqual(self.segs(uid)[-2:], [(WORK, at(10, 12, 29), at(10, 14)), (TRAVEL, at(10, 14), None)])

    def test_a_switch_may_lie_ahead_by_half_the_quantum(self):
        # #179 R1: kept as rounded, not refused or changed for lying ahead; nothing elapsed goes negative
        from activitytrack import texts
        uid = self.start(start_ms=at(9, 40))
        self.now = at(10, 7, 30)
        self.work.switch(uid, PAUSE)
        work, pause = self.work.segments(uid)
        self.assertEqual(pause["startUtcMs"] - self.now, 150_000)
        self.assertEqual(self.work.totals(uid)[PAUSE], 0)
        self.assertEqual(texts.hm(self.now - pause["startUtcMs"]), "0:00")
        state, since = self.work.display(self.store.get("entry", uid))
        self.assertEqual((state, model.to_ms(since)), (PAUSE, at(10, 10)))
        self.assertEqual(self.work.busy(date(2026, 11, 11)), [(9 * 60 + 40, 10 * 60 + 10)])
        # the times left as they are may be saved again
        self.work.edit_segment(work["uid"], work["startUtcMs"], work["endUtcMs"], WORK)
        self.assertEqual(self.segs(uid)[0], (WORK, at(9, 40), at(10, 10)))
        # a time moved ahead is still refused
        with self.assertRaises(WorkError) as err:
            self.work.edit_segment(work["uid"], work["startUtcMs"], at(10, 11), WORK)
        self.assertEqual(err.exception.code, "future_time")

    def test_a_switch_is_never_before_the_open_segment_even_ahead(self):
        # #179 R3, the owner's example: work and pause 10, travel 5
        self.work.quanta.update({WORK: 10, PAUSE: 10, TRAVEL: 5})
        uid = self.start(start_ms=at(9))
        self.now = at(10, 5)
        self.work.switch(uid, PAUSE)
        self.assertEqual(self.segs(uid), [(WORK, at(9), at(10, 10)), (PAUSE, at(10, 10), None)])
        # at 10:06 the nearest of 5 is 10:05, before the pause: not stored before it, and the pause, which
        # has not begun, goes rather than end where it starts
        self.now = at(10, 6)
        self.work.switch(uid, TRAVEL)
        self.assertEqual(self.segs(uid), [(WORK, at(9), at(10, 10)), (TRAVEL, at(10, 10), None)])
        self.assertEqual(self.store.get("entry", uid)["mode"], TRAVEL)
        # a typed instant before the open segment is refused when exact, else brought to its start
        self.now = at(10, 8)
        with self.assertRaises(WorkError) as err:
            self.work.switch(uid, WORK, at=at(10, 7), exact=True)
        self.assertEqual(err.exception.code, "before_segment")

    def test_the_stop_is_capped_at_a_day_ahead(self):
        # #179 R5: one limit, 24 hours
        self.assertEqual(model.FUTURE_CLOSE_MS, 24 * 60 * MIN)
        uid = self.start()
        self.work.quanta[WORK] = 120
        self.now = at(10, 20)
        self.work.stop(uid)
        self.assertEqual(self.segs(uid)[0][2], at(12))
        # a quantum that rounds up beyond the day: capped at it
        other = self.start()
        self.work.quanta[WORK] = 10 ** 6
        self.assertGreater(model.round_up(self.now, 10 ** 6, ROME), self.now + 24 * 60 * MIN)
        self.work.stop(other)
        self.assertEqual(self.segs(other)[0][2], self.now + 24 * 60 * MIN)

    def test_the_quantum_is_the_kinds(self):
        uid = self.start(mode=TRAVEL)
        self.assertEqual(self.segs(uid)[0][1], at(10, 5))
        self.now = at(10, 31)
        self.work.stop(uid)
        self.assertEqual(self.segs(uid)[0][2], at(10, 35))


class FutureAndNoticesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = at(10)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        self.store.write("client", "c", {"name": "C", "archived": False}, 1)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def start(self, **kw):
        return self.work.start(StartRequest(client="c", activity="x", **kw))

    def test_a_stop_up_to_a_day_ahead(self):
        uid = self.start(start_ms=at(9))
        self.work.stop(uid, at=at(10, 45), exact=True)
        e = self.store.get("entry", uid)
        self.assertEqual((e["status"], e["closedUtcMs"]), (CLOSED, self.now))
        self.assertEqual(self.work.display(e), ("DONE", datetime.fromtimestamp(at(10, 45) / 1000, ROME)))
        # #179 R5: up to 24 hours; beyond, refused, typed exactly or loosely
        other = self.start(start_ms=at(9, 30))
        for exact in (True, False):
            with self.assertRaises(WorkError) as err:
                self.work.stop(other, at=self.now + 24 * 60 * MIN + 1, exact=exact)
            self.assertEqual(err.exception.code, "future_time")
        self.assertEqual(self.store.get("entry", other)["status"], RUNNING)
        self.work.stop(other, at=at(9, day=12))
        self.assertEqual(self.work.segments(other)[0]["endUtcMs"], at(9, day=12))

    def test_not_with_locked_times(self):
        self.work.policy = policy(settings={"editableTimes": {"value": False, "locked": True}})
        uid = self.start()
        with self.assertRaises(WorkError):
            self.work.stop(uid, at=at(10, 30), exact=True)

    def test_the_last_segment_may_end_ahead_the_others_not(self):
        uid = self.start(start_ms=at(8), end_ms=at(9))
        self.work.add_segment(uid, at(9, 10), at(9, 30), TRAVEL)
        first, last = self.work.segments(uid)
        self.work.edit_segment(last["uid"], last["startUtcMs"], at(10, 40), TRAVEL)
        self.work.edit_segment(last["uid"], last["startUtcMs"], at(9, day=12), TRAVEL)
        with self.assertRaises(WorkError):
            self.work.edit_segment(last["uid"], last["startUtcMs"], at(10, 1, day=12), TRAVEL)
        with self.assertRaises(WorkError):
            self.work.edit_segment(first["uid"], first["startUtcMs"], at(10, 30), WORK)
        with self.assertRaises(WorkError):
            self.work.add_segment(uid, at(10, 50), at(10, 55), WORK)

    def test_an_activity_starting_before_the_end_ahead_moves_it_back(self):
        a = self.start(start_ms=at(9))
        self.work.stop(a, at=at(10, 40), exact=True)
        self.assertEqual(self.work.notices, [])
        self.now = at(10, 20)
        self.start()
        self.assertEqual(self.work.segments(a)[0]["endUtcMs"], at(10, 20))
        self.assertEqual(self.work.notices, [("end_moved", a)])
        # a suspended pause may be suspended too (the machine allows it), and its end is never before its start
        self.assertEqual(transition(RUNNING, PAUSE, SUSPEND), (RUNNING, None, True, None))

    def test_a_past_start_leaves_an_end_ahead_that_starts_after_it(self):
        # an activity closed with an end ahead (10:40), started at 10:00; another typed from 9:00 to now
        a = self.start(start_ms=at(10))
        self.work.stop(a, at=at(10, 40), exact=True)
        self.now = at(10, 20)
        self.start(start_ms=at(9))
        # no overlap at 9:00: the end stays, the segment is not cut to nothing
        self.assertEqual(self.work.segments(a)[0]["endUtcMs"], at(10, 40))
        self.assertEqual(self.work.notices, [])

    def test_a_reopen_or_resume_moves_an_end_ahead_back(self):
        # #179 R2: the end ahead comes back to the new start, with the notice of another start
        a = self.start(start_ms=at(9))
        self.work.stop(a, at=at(10, 40), exact=True)
        self.now = at(10, 20)
        self.work.reopen(a, WORK)
        self.assertEqual([(s["startUtcMs"], s.get("endUtcMs")) for s in self.work.segments(a)],
                         [(at(9), at(10, 20)), (at(10, 20), None)])
        self.assertEqual(self.work.notices, [("end_moved", a)])
        # resume: suspended, and the end of its last segment then put ahead
        self.now = at(10, 30)
        self.work.suspend(a)
        last = self.work.segments(a)[-1]
        self.work.edit_segment(last["uid"], last["startUtcMs"], at(11), WORK)
        self.now = at(10, 35)
        self.work.resume(a, WORK)
        self.assertEqual([(s["startUtcMs"], s.get("endUtcMs")) for s in self.work.segments(a)][1:],
                         [(at(10, 20), at(10, 35)), (at(10, 35), None)])
        self.assertEqual(self.work.notices[-1], ("end_moved", a))

    def test_a_segment_wholly_ahead_goes_with_a_notice(self):
        # #179 R4: the declared return trip, and a start at or before it
        w = self.work
        uid = self.start(start_ms=at(8), mode=TRAVEL)
        self.now = at(8, 40)
        w.switch(uid, WORK)
        self.now = at(11)
        w.stop(uid, return_trip=True)
        self.assertEqual([s["kind"] for s in w.segments(uid)], [TRAVEL, WORK, TRAVEL])
        # reopened at the same instant: cut at its start the return would be nothing
        w.reopen(uid, WORK)
        self.assertEqual([(s["kind"], s["startUtcMs"], s.get("endUtcMs")) for s in w.segments(uid)][1:],
                         [(WORK, at(8, 40), at(11)), (WORK, at(11), None)])
        self.assertEqual(w.notices, [("return_removed", uid)])
        # another segment ahead (from another device) and another activity started before it
        self.now = at(11, 10)
        w.stop(uid)
        self.store.write("segment", "ahead", {"entryUid": uid, "kind": WORK, "startUtcMs": at(11, 30),
                                              "endUtcMs": at(11, 45), "zoneId": "Europe/Rome",
                                              "source": "MANUAL", "planned": False}, 1)
        other = self.start()
        self.assertIsNone(self.store.get("segment", "ahead"))
        self.assertEqual(w.notices[-1], ("segment_removed", uid))
        self.assertEqual(self.store.get("entry", other)["status"], RUNNING)
        from activitytrack import texts
        self.assertEqual(texts.notice_text("return_removed"), "Il viaggio di ritorno previsto è stato rimosso.")
        self.assertEqual(texts.notice_text("segment_removed"), "Un tratto previsto è stato rimosso.")

    def test_travel_from_work_starts_nothing_new_with_planned_only(self):
        uid = self.start(start_ms=at(9))
        self.now = at(10)
        self.work.policy = policy(entries={"source": "plannedOnly"})
        with self.assertRaises(WorkError) as err:
            self.work.travel_from_work(uid, True)
        self.assertEqual(err.exception.code, "planned_only")
        self.assertEqual(self.store.get("entry", uid)["status"], RUNNING)
        # the same activity may still travel
        self.assertEqual(self.work.travel_from_work(uid, False), uid)

    def test_travel_back_switches_or_resumes(self):
        uid = self.start(start_ms=at(8))
        self.now = at(9)
        self.work.travel_back(uid)
        self.assertEqual(self.work.open_segment(uid)["kind"], TRAVEL)
        self.now = at(9, 30)
        self.work.suspend(uid)
        self.now = at(9, 40)
        self.work.travel_back(uid)
        self.assertEqual([s["kind"] for s in self.work.segments(uid)], [WORK, TRAVEL, TRAVEL])

    def test_travel_from_work_asks(self):
        uid = self.start(start_ms=at(9))
        self.assertTrue(self.work.asks_completion(uid))
        self.now = at(10, 30)
        # no: the same activity switches
        self.assertEqual(self.work.travel_from_work(uid, False), uid)
        self.assertEqual(self.work.open_segment(uid)["kind"], TRAVEL)
        self.assertFalse(self.work.asks_completion(uid))
        with self.assertRaises(WorkError):
            self.work.travel_from_work(uid, True)
        # yes: it closes and a new anonymous one travels from the same instant
        other = self.start(start_ms=at(10))
        self.now = at(10, 45)
        new = self.work.travel_from_work(other, True)
        self.assertNotEqual(new, other)
        closed, running = self.store.get("entry", other), self.store.get("entry", new)
        self.assertEqual((closed["status"], running["status"], running["mode"], running.get("clientUid")),
                         (CLOSED, RUNNING, TRAVEL, None))
        self.assertEqual(self.work.segments(other)[0]["endUtcMs"], self.work.segments(new)[0]["startUtcMs"])

    def test_the_return_trip(self):
        w = self.work
        uid = self.start(start_ms=at(8), mode=TRAVEL)
        self.assertFalse(w.return_trip_offered(uid))
        self.now = at(8, 40)
        w.switch(uid, WORK)
        outbound = w.segments(uid)[0]
        self.store.write("segment", outbound["uid"], {"kmEstimated": 31.5, "kmDeclared": 30.0}, 1)
        self.assertTrue(w.return_trip_offered(uid))
        self.assertTrue(w.return_trip_addable(uid))
        self.now = at(11)
        self.assertEqual(w.stop(uid, return_trip=True), CLOSED)
        work, back = w.segments(uid)[1:]
        self.assertEqual((work["kind"], work["endUtcMs"]), (WORK, at(11)))
        self.assertEqual((back["kind"], back["startUtcMs"], back["endUtcMs"], back["source"]),
                         (TRAVEL, at(11), at(11, 40), "MANUAL"))
        self.assertEqual((back["kmEstimated"], back["kmDeclared"]), (31.5, 30.0))
        self.assertEqual(self.store.get("entry", uid)["closedUtcMs"], self.now)
        # a return after the work: not offered again; nor without a travel before the work
        again = self.start(start_ms=at(10, 50))
        self.assertFalse(w.return_trip_offered(again))
        with self.assertRaises(WorkError):
            w.stop(again, return_trip=True)

    def test_the_return_trip_choices(self):
        w = self.work
        uid = self.start(start_ms=at(7), mode=TRAVEL)
        self.now = at(7, 30)
        w.switch(uid, WORK)
        self.now = at(9)
        w.switch(uid, PAUSE)
        self.now = at(9, 15)
        w.switch(uid, WORK)
        self.assertTrue(w.return_trip_offered(uid))
        # choice 1: a travel in the same activity, then no more return to offer
        self.now = at(10)
        w.switch(uid, TRAVEL)
        self.assertFalse(w.return_trip_offered(uid))
        # without editable times choice 3 is not there
        other = self.start(start_ms=at(7), mode=TRAVEL)
        self.now = at(7, 20)
        w.switch(other, WORK)
        w.policy = policy(settings={"editableTimes": {"value": False, "locked": True}})
        self.assertTrue(w.return_trip_offered(other))
        self.assertFalse(w.return_trip_addable(other))
        with self.assertRaises(WorkError):
            w.stop(other, return_trip=True)

    def test_a_reopen_with_rounding_drops_the_return_ahead(self):
        # #179 R2 and R4 with saving rounded times: the work's end ahead moves back, the return goes
        w = self.work
        w.round_stored = True
        self.now = at(8, 3)
        uid = self.start(mode=TRAVEL)
        self.now = at(8, 44)
        w.switch(uid, WORK)
        self.now = at(11, 1)
        w.stop(uid, return_trip=True)
        self.assertEqual([s["kind"] for s in w.segments(uid)], [TRAVEL, WORK, TRAVEL])
        self.now = at(11, 5)
        w.reopen(uid, WORK)
        self.assertEqual([(s["kind"], s["startUtcMs"], s.get("endUtcMs")) for s in w.segments(uid)][1:],
                         [(WORK, at(8, 45), at(11)), (WORK, at(11), None)])
        self.assertEqual(sorted(w.notices), [("end_moved", uid), ("return_removed", uid)])

    def test_the_return_trip_with_rounding_keeps_the_outbound_exact_duration(self):
        w = self.work
        w.round_stored = True
        self.now = at(8, 3)
        uid = self.start(mode=TRAVEL)
        self.now = at(8, 44)
        w.switch(uid, WORK)
        outbound = w.segments(uid)[0]
        duration = outbound["endUtcMs"] - outbound["startUtcMs"]
        self.now = at(11, 1)
        w.stop(uid, return_trip=True)
        work, back = w.segments(uid)[1:]
        self.assertEqual(work["endUtcMs"], at(11, 15))
        self.assertEqual((back["startUtcMs"], back["endUtcMs"] - back["startUtcMs"]), (at(11, 15), duration))


class AbsenceTest(unittest.TestCase):
    """ACTIVITY-MODEL.md 3.1."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = at(10)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        for dow in range(1, 6):
            self.store.write("workSchedule", f"w{dow}", {"dayOfWeek": dow, "startMin": 510, "endMin": 1050}, 1)
        # Friday: no lunch break
        for dow in range(1, 5):
            self.store.write("lunchBreak", f"l{dow}", {"dayOfWeek": dow, "startMin": 780, "endMin": 840}, 1)
        self.store.write("dayOff", "d1", {"date": "2026-11-17", "note": None}, 1)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def segs(self, uid):
        return [(datetime.fromtimestamp(s["startUtcMs"] / 1000, ROME).strftime("%d %H:%M"),
                 datetime.fromtimestamp(s["endUtcMs"] / 1000, ROME).strftime("%H:%M"))
                for s in self.work.segments(uid)]

    def test_working_days_of_the_schedule_with_the_lunch_break(self):
        # Thursday 12 to Tuesday 17 (a day off), across a weekend
        uid = self.work.add_absence(date(2026, 11, 12), date(2026, 11, 17), absence_kind="HOLIDAY", notes="mare")
        e = self.store.get("entry", uid)
        self.assertEqual((e["kind"], e["status"], e["mode"], e["absenceKind"], e["companyUid"], e["notes"]),
                         (ABSENCE, CLOSED, None, "HOLIDAY", None, "mare"))
        self.assertEqual(self.segs(uid), [("12 08:30", "13:00"), ("12 14:00", "17:30"), ("13 08:30", "17:30"),
                                          ("16 08:30", "13:00"), ("16 14:00", "17:30")])
        self.assertEqual(self.work.display(e)[0], "ABSENCE")
        self.assertTrue(all(s["kind"] == ABSENCE and not s["planned"] for s in self.work.segments(uid)))
        self.assertEqual(self.work.totals(uid)[ABSENCE], (270 + 210 + 540 + 270 + 210) * MIN)

    def test_with_times_one_segment_a_day_on_the_working_days(self):
        # Thursday to Tuesday: the weekend has no schedule, the 17th is a day off
        uid = self.work.add_absence(date(2026, 11, 12), date(2026, 11, 17), start_min=600, end_min=720,
                                    absence_kind="LEAVE")
        self.assertEqual(self.segs(uid), [("12 10:00", "12:00"), ("13 10:00", "12:00"), ("16 10:00", "12:00")])
        # a single day with times is taken as chosen (as the app and the server): a Saturday, a day off
        sat = self.work.add_absence(date(2026, 11, 14), start_min=600, end_min=720)
        self.assertEqual(self.segs(sat), [("14 10:00", "12:00")])
        off = self.work.add_absence(date(2026, 11, 17), start_min=600, end_min=720)
        self.assertEqual(self.segs(off), [("17 10:00", "12:00")])

    def test_without_any_schedule_only_the_days_off_are_skipped(self):
        for r in self.store.all("workSchedule"):
            self.store.delete("workSchedule", r["uid"], 1)
        uid = self.work.add_absence(date(2026, 11, 14), date(2026, 11, 17), start_min=600, end_min=720)
        self.assertEqual(self.segs(uid), [("14 10:00", "12:00"), ("15 10:00", "12:00"), ("16 10:00", "12:00")])

    def test_rules(self):
        for kw, code in ((dict(first=date(2026, 11, 14)), "no_working_days"),
                         (dict(first=date(2026, 11, 12), start_min=600), "invalid_time"),
                         (dict(first=date(2026, 11, 12), start_min=700, end_min=600), "invalid_range"),
                         (dict(first=date(2026, 11, 12), last=date(2026, 11, 10)), "invalid_range"),
                         (dict(first=date(2026, 11, 12), absence_kind="NOPE"), "illegal")):
            with self.assertRaises(WorkError) as err:
                self.work.add_absence(**kw)
            self.assertEqual(err.exception.code, code, kw)
        # nothing written by a refused one
        self.assertEqual(self.store.all("entry"), [])

    def test_changing_an_absence_replaces_its_segments(self):
        uid = self.work.add_absence(date(2026, 11, 12), date(2026, 11, 13))
        self.work.save_absence(uid, date(2026, 11, 12), company=None, start_min=480, end_min=540)
        self.assertEqual(self.segs(uid), [("12 08:00", "09:00")])
        self.assertEqual(self.work.save_absence(uid, date(2026, 11, 13)), uid)
        # a Friday: no lunch break
        self.assertEqual(self.segs(uid), [("13 08:30", "17:30")])
        # an absence has no commands, and is not a TODO or running activity
        for call in (lambda: self.work.tick(uid), lambda: self.work.reopen(uid), lambda: self.work.stop(uid),
                     lambda: self.work.start_todo(uid)):
            with self.assertRaises(WorkError):
                call()
        self.assertEqual(self.work.running(), [])
        self.assertEqual(self.work.todo_list(), [])
        # its segments are absence ones, may lie ahead, and no other kind fits
        seg = self.work.segments(uid)[0]
        self.work.edit_segment(seg["uid"], at(8, day=20), at(12, day=20), ABSENCE)
        with self.assertRaises(WorkError):
            self.work.edit_segment(seg["uid"], at(8, day=20), at(12, day=20), WORK)
        self.work.delete(uid)
        self.assertEqual(self.work.display(self.store.get("entry", uid))[0], "DELETED")

    def test_the_reminder_shortcut(self):
        uid = self.work.absent_all_day(date(2026, 11, 12))
        self.assertEqual(self.store.get("entry", uid)["kind"], ABSENCE)
        self.assertEqual(len(self.segs(uid)), 2)


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
