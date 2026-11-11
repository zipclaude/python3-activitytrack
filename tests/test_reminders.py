# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from activitytrack.policy import Policy
from activitytrack.reminders import (
    CONFIRM, DAY_START, DEFAULTS, PAUSE_KIND, PLANNED, PLANNED_END, Planner, Reminder, Sources, config_of, easter,
    italian_holiday,
)
from activitytrack.store import Store
from activitytrack.work import PAUSE, RUNNING, StartRequest, WORK, Work

ROME = ZoneInfo("Europe/Rome")
# Monday 2 November 2026
MON = date(2026, 11, 2)


def at(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ROME)


SCHEDULE = [(dow, 9 * 60, 13 * 60) for dow in range(1, 6)] + [(dow, 14 * 60, 18 * 60) for dow in range(1, 6)]
LUNCH = [(dow, 13 * 60, 14 * 60) for dow in range(1, 6)]


class HolidaysTest(unittest.TestCase):
    def test_holidays(self):
        self.assertEqual(easter(2026), date(2026, 4, 5))
        self.assertTrue(italian_holiday(date(2026, 4, 6)))
        self.assertTrue(italian_holiday(date(2026, 10, 4)))
        self.assertFalse(italian_holiday(date(2025, 10, 4)))
        self.assertTrue(italian_holiday(date(2026, 12, 26)))
        self.assertFalse(italian_holiday(MON))


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.p = Planner(dict(DEFAULTS), SCHEDULE, LUNCH, ROME, days_off={date(2026, 11, 3)})

    def test_confirm_and_its_limit(self):
        e = ("e1", RUNNING, WORK, at(MON, 9), 0, None)
        self.assertEqual(self.p.planned([e], [], at(MON, 9, 10)), [confirm_at(at(MON, 9, 30))])
        e = ("e1", RUNNING, WORK, at(MON, 9), 2, None)
        self.assertEqual(self.p.planned([e], [], at(MON, 9, 10))[0].at, at(MON, 10, 30))
        self.p.config["maxConfirmations"] = 2
        self.assertEqual(self.p.planned([e], [], at(MON, 9, 10)), [])

    def test_pause_out_of_lunch(self):
        e = ("e1", RUNNING, PAUSE, at(MON, 12, 50), 0, None)
        self.assertEqual(self.p.planned([e], [], at(MON, 12, 55))[0].at, at(MON, 14))
        self.assertEqual(self.p.planned([e], [], at(MON, 12, 55))[0].kind, PAUSE_KIND)

    def test_planned_todo_and_end(self):
        todo = ("t1", at(MON, 15), at(MON, 16), None)
        self.assertEqual(self.p.planned([], [todo], at(MON, 10))[0], planned_at("t1", at(MON, 14, 45)))
        # reminded for this start: not again; a new start: again
        self.assertEqual(self.p.planned([], [("t1", at(MON, 15), at(MON, 16), at(MON, 15))], at(MON, 10)), [])
        e = ("e1", RUNNING, WORK, None, 0, at(MON, 17))
        self.assertEqual(self.p.planned([e], [], at(MON, 10))[0].kind, PLANNED_END)

    def test_day_start(self):
        # Monday: 9:00 + 15 min
        r = self.p.planned([], [], at(MON, 8), day_start=(None, set()))
        self.assertEqual(r[0], day_start_at(MON, at(MON, 9, 15)))
        self.assertEqual(self.p.due([], [], at(MON, 9, 20), day_start=(None, set()))[0].kind, DAY_START)
        # handled (an activity started): Tuesday is off, so Wednesday
        r = self.p.planned([], [], at(MON, 8), day_start=(None, {MON}))
        self.assertEqual(r[0].key, "2026-11-04")
        # after the working hours: not today
        r = self.p.planned([], [], at(MON, 19), day_start=(None, set()))
        self.assertEqual(r[0].key, "2026-11-04")
        self.assertEqual(self.p.next([], [], at(MON, 8), day_start=(None, set())), at(MON, 9, 15))

    def test_config(self):
        p = Policy({"format": "activitytrack-policy", "schema": 1,
                    "settings": {"confirmIntervalMin": {"value": 60, "locked": True}, "pauseIntervalMin": 5}})
        c = config_of(p, {"confirmIntervalMin": 20, "planLeadMin": 5})
        # locked: the policy's; below the limit: the limit; the user's own where free
        self.assertEqual((c["confirmIntervalMin"], c["pauseIntervalMin"], c["planLeadMin"]), (60, 10, 5))


def confirm_at(t):
    return Reminder(CONFIRM, "e1", t)


def planned_at(uid, t):
    return Reminder(PLANNED, uid, t)


def day_start_at(d, t):
    return Reminder(DAY_START, d.isoformat(), t)


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "d.sqlite3")
        self.now = int(at(MON, 9).timestamp() * 1000)
        self.work = Work(self.store, clock=lambda: self.now, zone="Europe/Rome")
        self.src = Sources(self.work)
        self.store.pulled({"type": "client", "uid": "c", "rev": 1, "fields": {"name": "Acme", "archived": False}})

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_confirmation_follows_the_segments(self):
        uid = self.work.start(StartRequest(client="c", activity="x"))
        (e,) = self.src.entries(ROME)
        self.assertEqual((e[3], e[4]), (at(MON, 9), 0))
        self.src.reminded(Reminder(CONFIRM, uid, at(MON, 9, 30)), self.now)
        self.assertEqual(self.src.entries(ROME)[0][4], 1)
        self.now += 40 * 60_000
        self.src.confirmed(uid, self.now)
        self.assertEqual(self.src.entries(ROME)[0][3:5], (at(MON, 9, 40), 0))
        # a pause: a new segment, counted from its start
        self.now += 10 * 60_000
        self.work.switch(uid, PAUSE)
        self.assertEqual(self.src.entries(ROME)[0][3:5], (at(MON, 9, 50), 0))
        self.work.stop(uid)
        self.assertEqual(self.src.entries(ROME), [])
        self.assertEqual(self.src.state()["confirm"], {})

    def test_planned_todos_and_handled_days(self):
        t = self.work.save_todo(None, "Call", plan_date=MON, plan_min=15 * 60, duration_min=30)
        self.work.save_todo(None, "Whole day", plan_date=MON)
        (p,) = self.src.planned_todos(ROME, at(MON, 10))
        self.assertEqual((p[0], p[1], p[3]), (t, at(MON, 15), None))
        self.src.reminded(Reminder(PLANNED, t, at(MON, 14, 45)), self.now)
        self.assertEqual(self.src.planned_todos(ROME, at(MON, 10))[0][3], at(MON, 15))
        self.assertEqual(self.src.handled_days(ROME, MON), set())
        self.work.start(StartRequest(client="c", activity="x", todo=t))
        self.assertEqual(self.src.planned_todos(ROME, at(MON, 10)), [])
        self.assertEqual(self.src.handled_days(ROME, MON), {MON})
        self.assertEqual(self.src.day_start_state(ROME, MON)[0], None)
        # an absence handles its days, a planned segment does not
        other = self.work.save_todo(None, "Later")
        self.work.add_segment(other, self.now + 30 * 24 * 3_600_000, self.now + 31 * 24 * 3_600_000, WORK, planned=True)
        absence = self.work.add_absence(date(2026, 11, 9), start_min=540, end_min=600)
        self.assertEqual(self.src.handled_days(ROME, MON), {MON, date(2026, 11, 9)})
        self.work.delete(absence)
        self.assertEqual(self.src.handled_days(ROME, MON), {MON})
        self.store.pulled({"type": "workSchedule", "uid": "w1", "rev": 2,
                           "fields": {"dayOfWeek": 1, "startMin": 540, "endMin": 780}})
        self.assertEqual(self.src.windows("workSchedule"), [(1, 540, 780)])
