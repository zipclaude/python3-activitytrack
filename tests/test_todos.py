# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from activitytrack.policy import Policy
from activitytrack.store import Store
from activitytrack.todos import (
    DONE, IGNORED, MISSING, TODO, Todos, moment, overdue, plan_bounds, postponed, sections, state,
)
from activitytrack.work import STOP, StartRequest, Work, WorkError

ROME = ZoneInfo("Europe/Rome")


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ROME)


class PlanTest(unittest.TestCase):
    def test_plans(self):
        day = {"planDate": "2026-11-02"}
        self.assertEqual(plan_bounds(day, ROME), (at(2026, 11, 2), at(2026, 11, 3), True))
        block = {"planDate": "2026-11-02", "planStartMin": 900, "durationMin": 30}
        self.assertEqual(plan_bounds(block, ROME)[:2], (at(2026, 11, 2, 15), at(2026, 11, 2, 15, 30)))
        self.assertIsNone(plan_bounds({}, ROME))
        self.assertEqual(moment({"dueDate": "2026-11-05", "dueTimeMin": 600}, ROME), at(2026, 11, 5, 10))

    def test_overdue_and_state(self):
        block = {"planDate": "2026-11-02", "planStartMin": 900, "durationMin": 30}
        block, whole, due = [dict(x, status="TODO") for x in (block, {"planDate": "2026-11-02"},
                                                             {"dueDate": "2026-11-01"})]
        self.assertFalse(overdue(block, at(2026, 11, 2, 15, 30), ROME))
        self.assertTrue(overdue(block, at(2026, 11, 2, 15, 31), ROME))
        # a whole day is over from the next day
        self.assertFalse(overdue(whole, at(2026, 11, 2, 23, 59), ROME))
        self.assertTrue(overdue(whole, at(2026, 11, 3), ROME))
        self.assertTrue(overdue(due, at(2026, 11, 2, 0, 1), ROME))
        # never once closed or deleted
        self.assertFalse(overdue(dict(due, status="CLOSED"), at(2026, 11, 2, 0, 1), ROME))
        self.assertFalse(overdue(dict(due, deletedUtcMs=1), at(2026, 11, 2, 0, 1), ROME))
        todo = {"status": "TODO"}
        self.assertEqual(state({"status": "CLOSED", "planDate": "2026-01-01"}, at(2026, 11, 2), ROME), "done")
        self.assertEqual(state(dict(todo, deletedUtcMs=5), at(2026, 11, 2), ROME), "ignored")
        self.assertEqual(state(dict(todo, planDate="2026-12-01"), at(2026, 11, 2), ROME), "planned")
        self.assertEqual(state(dict(todo, dueDate="2026-12-01"), at(2026, 11, 2), ROME), "planned")
        self.assertEqual(state(dict(todo, planDate="2026-01-01"), at(2026, 11, 2), ROME), "overdue")
        self.assertEqual(state(todo, at(2026, 11, 2), ROME), "open")
        # planned segments rule over the plan (2.3)
        ahead = [{"planned": True, "startUtcMs": 1, "endUtcMs": at(2026, 12, 1).timestamp() * 1000}]
        self.assertEqual(state(dict(todo, planDate="2026-01-01"), at(2026, 11, 2), ROME, ahead), "planned")

    def test_postponed(self):
        block = {"planDate": "2026-11-02", "planStartMin": 900, "durationMin": 30}
        # before the start: after the start
        self.assertEqual(postponed(block, 15, at(2026, 11, 2, 14), ROME),
                         {"planDate": "2026-11-02", "planStartMin": 915})
        # once started: after now, from the next whole minute
        now = datetime(2026, 11, 2, 15, 20, 30, tzinfo=ROME)
        self.assertEqual(postponed(block, 15, now, ROME), {"planDate": "2026-11-02", "planStartMin": 936})
        # across midnight
        late = {"planDate": "2026-11-02", "planStartMin": 1430}
        self.assertEqual(postponed(late, 30, at(2026, 11, 2, 10), ROME),
                         {"planDate": "2026-11-03", "planStartMin": 20})
        # a whole day moves by days
        self.assertEqual(postponed({"planDate": "2026-11-02"}, 15, at(2026, 11, 2, 10), ROME),
                         {"planDate": "2026-11-03"})
        self.assertIsNone(postponed({}, 15, at(2026, 11, 2), ROME))


class SectionsTest(unittest.TestCase):
    def test_the_home_list(self):
        now = at(2026, 11, 2, 10)
        t = {"kind": "ACTIVITY", "status": "TODO"}
        todos = [
            dict(t, uid="late", activity="Late", planDate="2026-11-01", planStartMin=600),
            dict(t, uid="today", activity="Today", planDate="2026-11-02", planStartMin=900),
            dict(t, uid="tomorrow", activity="Tomorrow", dueDate="2026-11-03"),
            dict(t, uid="far", activity="Far", planDate="2026-12-01"),
            dict(t, uid="nodate", activity="No date", priority=1),
            dict(t, uid="urgent", activity="Urgent", priority=4),
            dict(t, uid="done", activity="Done", status="CLOSED", closedUtcMs=at(2026, 11, 2, 9).timestamp() * 1000),
            dict(t, uid="ign", activity="Ign", deletedUtcMs=at(2026, 11, 2, 8).timestamp() * 1000),
            dict(t, uid="busy", activity="Busy"),
        ]
        missing = ({"uid": "e1", "status": "CLOSED", "_display": "INCOMPLETE"}, at(2026, 11, 2, 9))
        closed = ({"uid": "e2", "status": "CLOSED", "_display": "DONE", "tags": ["x"]}, at(2026, 11, 2, 8))

        def uids(out):
            return {s: [i[1]["uid"] for i in items] for s, items in out}

        out = uids(sections(todos, [missing, closed], {TODO, MISSING}, -1, now=now, tz=ROME, working={"busy"}))
        # due tomorrow without time: its moment is midnight, the edge of "today" (as the app: isAfter)
        self.assertEqual(out, {"overdue": ["late"], "missing": ["e1"], "today": ["today"], "next": ["tomorrow"],
                               "no_date": ["urgent", "nodate"]})
        out = uids(sections(todos, [missing, closed], {TODO, DONE, IGNORED}, 72, now=now, tz=ROME))
        self.assertEqual(out["today"], ["today", "done", "e2"])
        self.assertEqual(out["next"], ["tomorrow"])
        self.assertEqual(out["ignored"], ["ign"])
        # a tag shows only the activities
        out = uids(sections(todos, [missing, closed], {TODO, DONE}, 0, tag="x", now=now, tz=ROME))
        self.assertEqual(out, {"today": ["e2"]})


class TodosTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = int(at(2026, 11, 2, 10).timestamp() * 1000)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        self.todos = Todos(self.work)
        self.store.pulled({"type": "client", "uid": "lib", "rev": 1,
                           "fields": {"name": "Biblioteca", "companyUid": None, "archived": False}})

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def get(self, uid):
        return self.store.get("entry", uid)

    def test_new_and_changed(self):
        uid = self.todos.save(None, "  Rinnovo certificati ", client="biblioteca", refs="INC1\n\n https://x/1 ",
                              plan_date=date(2026, 11, 2), plan_min=900, duration_min=30, priority=2)
        t = self.get(uid)
        self.assertEqual((t["activity"], t["clientUid"], t["planDate"], t["planStartMin"], t["status"], t["kind"]),
                         ("Rinnovo certificati", "lib", "2026-11-02", 900, "TODO", "ACTIVITY"))
        refs = sorted((r["kind"], r["value"]) for r in self.store.all("externalRef") if r["entryUid"] == uid)
        self.assertEqual(refs, [("TICKET", "INC1"), ("URL", "https://x/1")])
        self.todos.save(uid, "Rinnovo", client="lib")
        t = self.get(uid)
        self.assertEqual((t["activity"], t["planDate"], t["priority"]), ("Rinnovo", None, 0))
        self.assertEqual([r for r in self.store.all("externalRef") if r["entryUid"] == uid], [])
        for kw, code in ((dict(title=""), "title_required"), (dict(plan_min=600), "plan_day_required"),
                         (dict(plan_date=date(2026, 11, 2), duration_min=30), "plan_time_required"),
                         (dict(priority=7), "invalid_priority")):
            kw.setdefault("title", "x")
            with self.assertRaises(WorkError) as e:
                self.todos.save(None, **kw)
            self.assertEqual(e.exception.code, code)

    def test_states(self):
        uid = self.todos.save(None, "x")
        self.todos.set_done(uid)
        t = self.get(uid)
        self.assertEqual((t["status"], t["closedUtcMs"]), ("CLOSED", self.now))
        self.assertEqual([x["uid"] for x in self.todos.all()], [uid])
        self.todos.set_done(uid, False)
        self.assertEqual(self.get(uid)["status"], "TODO")
        self.todos.set_ignored(uid)
        t = self.get(uid)
        self.assertEqual((t["status"], t["deletedUtcMs"]), ("TODO", self.now))
        self.assertEqual(self.work.todo_list(), [])
        self.assertEqual([x["uid"] for x in self.todos.all()], [uid])
        self.todos.reopen(uid)
        t = self.get(uid)
        self.assertEqual((t["status"], t["deletedUtcMs"], t["closedUtcMs"]), ("TODO", None, None))
        self.todos.set_done(uid)
        self.todos.reopen(uid)
        self.assertEqual(self.get(uid)["status"], "TODO")

    def test_postpone_and_policy(self):
        uid = self.todos.save(None, "x", plan_date=date(2026, 11, 2), plan_min=900)
        self.todos.postpone(uid, now=at(2026, 11, 2, 10))
        self.assertEqual(self.get(uid)["planStartMin"], 915)
        self.work.policy = Policy({"format": "activitytrack-policy", "schema": 1,
                                   "features": {"ignoreTodos": False, "deleteTodos": False, "planning": False},
                                   "settings": {"planSnoozeMin": 60}})
        for f in (lambda: self.todos.set_ignored(uid), lambda: self.todos.delete(uid),
                  lambda: self.todos.postpone(uid)):
            with self.assertRaises(WorkError):
                f()
        # an existing plan may stay when planning is off
        self.todos.save(uid, "y", plan_date=date(2026, 11, 2), plan_min=915)

    def test_delete_is_the_bin_also_for_an_imported_one(self):
        uid = self.todos.save(None, "x")
        self.todos.delete(uid)
        self.assertIsNotNone(self.get(uid)["deletedUtcMs"])
        self.store.pulled({"type": "entry", "uid": "imp", "rev": 2, "fields": {
            "kind": "ACTIVITY", "status": "TODO", "activity": "Ticket", "externalKey": "T-1"}})
        self.todos.delete("imp")
        self.assertEqual(self.get("imp")["deletedUtcMs"], self.now)

    def test_an_absence_is_not_a_todo(self):
        self.store.pulled({"type": "entry", "uid": "ab", "rev": 2, "fields": {
            "kind": "ABSENCE", "status": "CLOSED", "absenceKind": "HOLIDAY"}})
        self.assertEqual(self.todos.all(), [])
        with self.assertRaises(WorkError):
            self.todos.todo("ab")

    def test_home_and_startable(self):
        today = self.todos.save(None, "Oggi", client="lib", plan_date=date(2026, 11, 2), plan_min=900)
        self.todos.save(None, "Senza data")
        e = self.work.start(StartRequest(client="lib", activity="Rete"))
        self.work.command(e, STOP)
        no_client = self.work.start(StartRequest(client="lib", activity="Cavi"))
        self.work.command(no_client, STOP)
        self.work.edit(no_client, client="")
        home = dict(self.todos.home(now=at(2026, 11, 2, 10)))
        self.assertEqual([i[1]["uid"] for i in home["today"]], [today])
        self.assertIn("no_date", home)
        # a closed activity without what it needs is "missing"; a complete one is done
        self.assertEqual([i[1]["uid"] for i in home["missing"]], [no_client])
        done = dict(self.todos.home((DONE,), now=at(2026, 11, 2, 10)))
        self.assertEqual([i[1]["uid"] for i in done["today"]], [e])
        self.assertEqual([t["uid"] for t in self.todos.startable(now=at(2026, 11, 2, 10))], [today])
        # started: shown by its activity, not in the list
        self.work.start(StartRequest(todo=today, client="lib"))
        self.assertNotIn("today", dict(self.todos.home(now=at(2026, 11, 2, 10))))
