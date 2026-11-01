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
        self.assertFalse(overdue(block, at(2026, 11, 2, 15, 30), ROME))
        self.assertTrue(overdue(block, at(2026, 11, 2, 15, 31), ROME))
        # a whole day is over from the next day
        self.assertFalse(overdue({"planDate": "2026-11-02"}, at(2026, 11, 2, 23, 59), ROME))
        self.assertTrue(overdue({"planDate": "2026-11-02"}, at(2026, 11, 3), ROME))
        self.assertTrue(overdue({"dueDate": "2026-11-01"}, at(2026, 11, 2, 0, 1), ROME))
        self.assertEqual(state({"done": True, "planDate": "2026-01-01"}, at(2026, 11, 2), ROME), "done")
        self.assertEqual(state({"ignoredUtcMs": 5}, at(2026, 11, 2), ROME), "ignored")
        self.assertEqual(state({"planDate": "2026-12-01"}, at(2026, 11, 2), ROME), "planned")
        self.assertEqual(state({}, at(2026, 11, 2), ROME), "open")

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
        todos = [
            {"uid": "late", "title": "Late", "planDate": "2026-11-01", "planStartMin": 600},
            {"uid": "today", "title": "Today", "planDate": "2026-11-02", "planStartMin": 900},
            {"uid": "tomorrow", "title": "Tomorrow", "dueDate": "2026-11-03"},
            {"uid": "far", "title": "Far", "planDate": "2026-12-01"},
            {"uid": "nodate", "title": "No date", "priority": 1},
            {"uid": "done", "title": "Done", "done": True, "doneUtcMs": at(2026, 11, 2, 9).timestamp() * 1000},
            {"uid": "ign", "title": "Ign", "ignoredUtcMs": at(2026, 11, 2, 8).timestamp() * 1000},
            {"uid": "busy", "title": "Busy"},
        ]
        missing = ({"uid": "e1", "status": "TO_COMPLETE"}, at(2026, 11, 2, 9))
        closed = ({"uid": "e2", "status": "CLOSED", "tags": ["x"]}, at(2026, 11, 2, 8))

        def uids(out):
            return {s: [i[1]["uid"] for i in items] for s, items in out}

        out = uids(sections(todos, [missing, closed], {TODO, MISSING}, -1, now=now, tz=ROME, working={"busy"}))
        # due tomorrow without time: its moment is midnight, the edge of "today" (as the app: isAfter)
        self.assertEqual(out, {"overdue": ["late"], "missing": ["e1"], "today": ["today"], "next": ["tomorrow"],
                               "no_date": ["nodate"]})
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

    def test_new_and_changed(self):
        uid = self.todos.save(None, "  Rinnovo certificati ", client="biblioteca", refs="INC1\n\n https://x/1 ",
                              plan_date=date(2026, 11, 2), plan_min=900, duration_min=30, priority=2)
        t = self.store.get("todo", uid)
        self.assertEqual((t["title"], t["clientUid"], t["refs"], t["planDate"], t["planStartMin"], t["done"]),
                         ("Rinnovo certificati", "lib", "INC1\nhttps://x/1", "2026-11-02", 900, False))
        self.todos.save(uid, "Rinnovo", client="lib")
        t = self.store.get("todo", uid)
        self.assertEqual((t["title"], t["planDate"], t["priority"]), ("Rinnovo", None, None))
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
        first = self.store.get("todo", uid)["doneUtcMs"]
        self.now += 1000
        self.todos.set_done(uid)
        self.assertEqual(self.store.get("todo", uid)["doneUtcMs"], first)
        self.todos.set_ignored(uid)
        t = self.store.get("todo", uid)
        self.assertEqual((t["done"], t["ignoredUtcMs"]), (False, self.now))
        self.todos.reopen(uid)
        t = self.store.get("todo", uid)
        self.assertEqual((t["done"], t["ignoredUtcMs"], t["doneUtcMs"]), (False, None, None))

    def test_postpone_and_policy(self):
        uid = self.todos.save(None, "x", plan_date=date(2026, 11, 2), plan_min=900)
        self.todos.postpone(uid, now=at(2026, 11, 2, 10))
        self.assertEqual(self.store.get("todo", uid)["planStartMin"], 915)
        self.work.policy = Policy({"format": "activitytrack-policy", "schema": 1,
                                   "features": {"ignoreTodos": False, "deleteTodos": False, "planning": False},
                                   "settings": {"planSnoozeMin": 60}})
        for f in (lambda: self.todos.set_ignored(uid), lambda: self.todos.delete(uid),
                  lambda: self.todos.postpone(uid)):
            with self.assertRaises(WorkError):
                f()
        # an existing plan may stay when planning is off
        self.todos.save(uid, "y", plan_date=date(2026, 11, 2), plan_min=915)

    def test_delete_only_by_hand(self):
        uid = self.todos.save(None, "x")
        self.todos.delete(uid)
        self.assertIsNone(self.store.get("todo", uid))
        self.store.pulled({"type": "todo", "uid": "imp", "rev": 2,
                           "fields": {"title": "Ticket", "externalKey": "T-1", "done": False}})
        with self.assertRaises(WorkError):
            self.todos.delete("imp")

    def test_home_and_startable(self):
        today = self.todos.save(None, "Oggi", client="lib", plan_date=date(2026, 11, 2), plan_min=900)
        self.todos.save(None, "Senza data")
        e = self.work.start(StartRequest(client="lib", activity="Rete"))
        self.work.command(e, STOP)
        home = dict(self.todos.home(now=at(2026, 11, 2, 10)))
        self.assertEqual([i[1]["uid"] for i in home["today"]], [today])
        self.assertIn("no_date", home)
        self.assertEqual([t["uid"] for t in self.todos.startable(now=at(2026, 11, 2, 10))], [today])
        # started: shown by its activity, not in the list
        self.work.start(StartRequest(todo=today, client="lib"))
        self.assertNotIn("today", dict(self.todos.home(now=at(2026, 11, 2, 10))))
