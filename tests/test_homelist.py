# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from activitytrack.homelist import DONE, IGNORED, MISSING, TODO, home, sections, startable
from activitytrack.model import display, is_late, moment, plan_bounds, postponed
from activitytrack.policy import Policy
from activitytrack.store import Store
from activitytrack.work import StartRequest, Work, WorkError

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

    def test_late_and_displayed_state(self):
        block = {"planDate": "2026-11-02", "planStartMin": 900, "durationMin": 30}
        block, whole, due = [dict(x, status="TODO") for x in (block, {"planDate": "2026-11-02"},
                                                             {"dueDate": "2026-11-01"})]
        self.assertFalse(is_late(block, [], at(2026, 11, 2, 15, 30), ROME))
        self.assertTrue(is_late(block, [], at(2026, 11, 2, 15, 31), ROME))
        # a whole day is over from the next day
        self.assertFalse(is_late(whole, [], at(2026, 11, 2, 23, 59), ROME))
        self.assertTrue(is_late(whole, [], at(2026, 11, 3), ROME))
        self.assertTrue(is_late(due, [], at(2026, 11, 2, 0, 1), ROME))

        def shown(a, now, segs=()):
            return display(a, segs, now, ROME)[0]

        todo = {"status": "TODO"}
        self.assertEqual(shown({"status": "CLOSED", "planDate": "2026-01-01"}, at(2026, 11, 2)), "DONE")
        self.assertEqual(shown(dict(todo, deletedUtcMs=5), at(2026, 11, 2)), "DELETED")
        self.assertEqual(shown(dict(todo, planDate="2026-12-01"), at(2026, 11, 2)), "PLANNED")
        self.assertEqual(shown(dict(todo, dueDate="2026-12-01"), at(2026, 11, 2)), "PLANNED")
        self.assertEqual(shown(dict(todo, planDate="2026-01-01"), at(2026, 11, 2)), "LATE")
        self.assertEqual(shown(todo, at(2026, 11, 2)), "TODO")
        # planned segments rule over the plan (2.3)
        ahead = [{"planned": True, "startUtcMs": 1, "endUtcMs": at(2026, 12, 1).timestamp() * 1000}]
        self.assertEqual(shown(dict(todo, planDate="2026-01-01"), at(2026, 11, 2), ahead), "PLANNED")

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


class HomeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = int(at(2026, 11, 2, 10).timestamp() * 1000)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        self.store.pulled({"type": "client", "uid": "lib", "rev": 1,
                           "fields": {"name": "Biblioteca", "companyUid": None, "archived": False}})

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def get(self, uid):
        return self.store.get("entry", uid)

    def test_new_and_changed(self):
        uid = self.work.save_todo(None, "  Rinnovo certificati ", client="biblioteca", refs="INC1\n\n https://x/1 ",
                                  plan_date=date(2026, 11, 2), plan_min=900, duration_min=30, priority=2)
        t = self.get(uid)
        self.assertEqual((t["activity"], t["clientUid"], t["planDate"], t["planStartMin"], t["status"], t["kind"]),
                         ("Rinnovo certificati", "lib", "2026-11-02", 900, "TODO", "ACTIVITY"))
        refs = sorted((r["kind"], r["value"]) for r in self.store.all("externalRef") if r["entryUid"] == uid)
        self.assertEqual(refs, [("TICKET", "INC1"), ("URL", "https://x/1")])
        self.work.save_todo(uid, "Rinnovo", client="lib", refs="")
        t = self.get(uid)
        self.assertEqual((t["activity"], t["planDate"], t["priority"]), ("Rinnovo", None, 0))
        self.assertEqual([r for r in self.store.all("externalRef") if r["entryUid"] == uid], [])
        for kw, code in ((dict(activity=""), "title_required"), (dict(plan_min=600), "plan_day_required"),
                         (dict(plan_date=date(2026, 11, 2), duration_min=30), "plan_time_required"),
                         (dict(priority=7), "invalid_priority")):
            kw.setdefault("activity", "x")
            with self.assertRaises(WorkError) as e:
                self.work.save_todo(None, **kw)
            self.assertEqual(e.exception.code, code)

    def test_states(self):
        uid = self.work.save_todo(None, "x")
        self.work.tick(uid)
        t = self.get(uid)
        self.assertEqual((t["status"], t["closedUtcMs"]), ("CLOSED", self.now))
        self.work.untick(uid)
        self.assertEqual(self.get(uid)["status"], "TODO")
        self.work.delete(uid)
        t = self.get(uid)
        self.assertEqual((t["status"], t["deletedUtcMs"]), ("TODO", self.now))
        self.assertEqual(self.work.todo_list(), [])
        self.work.restore(uid)
        t = self.get(uid)
        self.assertEqual((t["status"], t["deletedUtcMs"], t["closedUtcMs"]), ("TODO", None, None))

    def test_postpone_and_policy(self):
        uid = self.work.save_todo(None, "x", plan_date=date(2026, 11, 2), plan_min=900)
        self.work.postpone(uid, now=at(2026, 11, 2, 10))
        self.assertEqual(self.get(uid)["planStartMin"], 915)
        self.work.policy = Policy({"format": "activitytrack-policy", "schema": 1,
                                   "features": {"ignoreTodos": False, "deleteTodos": False, "planning": False},
                                   "settings": {"planSnoozeMin": 60}})
        for f in (lambda: self.work.delete(uid), lambda: self.work.postpone(uid)):
            with self.assertRaises(WorkError):
                f()
        # an existing plan may stay when planning is off
        self.work.save_todo(uid, "y", plan_date=date(2026, 11, 2), plan_min=915)

    def test_an_imported_one_goes_to_the_bin_too(self):
        self.store.pulled({"type": "entry", "uid": "imp", "rev": 2, "fields": {
            "kind": "ACTIVITY", "status": "TODO", "activity": "Ticket", "externalKey": "T-1"}})
        self.work.delete("imp")
        self.assertEqual(self.get("imp")["deletedUtcMs"], self.now)

    def test_an_absence_is_not_in_the_list(self):
        self.store.pulled({"type": "entry", "uid": "ab", "rev": 2, "fields": {
            "kind": "ABSENCE", "status": "CLOSED", "absenceKind": "HOLIDAY"}})
        self.assertEqual(home(self.work, (TODO, MISSING, DONE, IGNORED), 0, now=at(2026, 11, 2, 10)), [])

    def test_home_and_startable(self):
        today = self.work.save_todo(None, "Oggi", client="lib", plan_date=date(2026, 11, 2), plan_min=900)
        self.work.save_todo(None, "Senza data")
        e = self.work.start(StartRequest(client="lib", activity="Rete"))
        self.work.stop(e)
        no_client = self.work.start(StartRequest(client="lib", activity="Cavi"))
        self.work.stop(no_client)
        self.work.edit(no_client, client="")
        now = at(2026, 11, 2, 10)
        listed = dict(home(self.work, now=now))
        self.assertEqual([i[1]["uid"] for i in listed["today"]], [today])
        self.assertEqual(listed["today"][0][2], "PLANNED")
        self.assertIn("no_date", listed)
        # a closed activity without what it needs is "missing"; a complete one is done
        self.assertEqual([i[1]["uid"] for i in listed["missing"]], [no_client])
        done = dict(home(self.work, (DONE,), now=now))
        self.assertEqual([i[1]["uid"] for i in done["today"]], [e])
        self.assertEqual([t["uid"] for t in startable(self.work, now=now)], [today])
        # started: shown by its activity, not in the list
        self.work.start(StartRequest(todo=today, client="lib"))
        self.assertNotIn("today", dict(home(self.work, now=now)))
