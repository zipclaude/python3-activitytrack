# SPDX-License-Identifier: GPL-3.0-or-later
import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from activitytrack import model

DATA = Path(__file__).with_name("data")
ROME = ZoneInfo("Europe/Rome")


def ms(text):
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def activity_of(case):
    """The shared case's activity as the store's record."""
    a = case["activity"]
    out = {"kind": a["kind"], "status": a["status"]}
    if a.get("client"):
        out["clientUid"] = "client"
    if a.get("absenceKind"):
        out["absenceKind"] = a["absenceKind"]
    if a.get("closedAt"):
        out["closedUtcMs"] = ms(a["closedAt"])
    if a.get("deletedAt"):
        out["deletedUtcMs"] = ms(a["deletedAt"])
    if a.get("plan"):
        out["planDate"] = a["plan"]["day"]
        out["planStartMin"] = a["plan"].get("startMin")
        out["durationMin"] = a["plan"].get("durationMin")
    if a.get("due"):
        out["dueDate"] = a["due"]["day"]
        out["dueTimeMin"] = a["due"].get("timeMin")
    return out


def segments_of(case):
    return [{"kind": s["kind"], "startUtcMs": ms(s["start"]), "planned": bool(s.get("planned")),
             "endUtcMs": ms(s["end"]) if s.get("end") else None} for s in case["segments"]]


class DisplayTest(unittest.TestCase):
    def test_the_shared_cases(self):
        data = json.loads((DATA / "activity-states.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["cases"]), 25)
        now = datetime.fromisoformat(data["now"])
        for case in data["cases"]:
            with self.subTest(case["name"]):
                state, since = model.display(activity_of(case), segments_of(case), now, data["zone"],
                                             case.get("missingRequired", False))
                expect = case["expect"]
                self.assertEqual(state, expect["state"])
                if expect["since"] is None:
                    self.assertIsNone(since)
                else:
                    self.assertEqual(since, datetime.fromisoformat(expect["since"]))

    def test_now_as_milliseconds(self):
        a = {"kind": "ACTIVITY", "status": "TODO", "dueDate": "2026-11-10"}
        self.assertEqual(model.display(a, [], ms("2026-11-11T10:00:00+01:00"), "Europe/Rome")[0], "LATE")


class AbsenceWindowsTest(unittest.TestCase):
    def test_overlapping_windows_merge_before_the_lunch_break(self):
        day = datetime(2026, 11, 11).date()
        schedule = [(3, 540, 780), (3, 720, 1080), (3, 1080, 1100), (4, 600, 700)]
        self.assertEqual(model.working_windows(day, schedule, [(3, 780, 840), (3, 800, 820)]),
                         [(540, 780), (840, 1100)])
        self.assertEqual(model.working_windows(day, schedule, []), [(540, 1100)])
        self.assertEqual(model.working_windows(day, [], []), [])

    def test_a_planned_segment_without_an_end_has_not_ended(self):
        a = {"kind": "ACTIVITY", "status": "TODO"}
        past = ms("2026-11-10T09:00:00+01:00")
        now = ms("2026-11-11T10:00:00+01:00")
        self.assertEqual(model.display(a, [{"kind": "WORK", "startUtcMs": past, "endUtcMs": past + 1, "planned": True}],
                                       now, "Europe/Rome")[0], "LATE")
        self.assertEqual(model.display(a, [{"kind": "WORK", "startUtcMs": past, "planned": True}],
                                       now, "Europe/Rome")[0], "PLANNED")


class RoundingTest(unittest.TestCase):
    def at(self, h, m=0, s=0):
        return int(datetime(2026, 11, 11, h, m, s, tzinfo=ROME).timestamp() * 1000)

    def test_down_up_nearest(self):
        t = self.at(10, 7, 30)
        self.assertEqual(model.round_down(t, 15, ROME), self.at(10))
        self.assertEqual(model.round_up(t, 15, ROME), self.at(10, 15))
        self.assertEqual(model.round_nearest(t, 15, ROME), self.at(10, 15))
        self.assertEqual(model.round_nearest(self.at(10, 7, 29), 15, ROME), self.at(10))
        # already on a multiple
        self.assertEqual(model.round_up(self.at(10, 15), 15, ROME), self.at(10, 15))
        self.assertEqual(model.round_down(self.at(10, 15), 15, ROME), self.at(10, 15))
        # limits
        self.assertEqual(model.round_down(t, 15, ROME, not_before=self.at(10, 3)), self.at(10, 3))
        self.assertEqual(model.round_up(t, 15, ROME, limit=self.at(10, 10)), self.at(10, 10))

    def test_on_the_local_clock(self):
        # a zone off by 5:45: the quarter hours are those of its clock
        kathmandu = ZoneInfo("Asia/Kathmandu")
        t = int(datetime(2026, 11, 11, 10, 7, tzinfo=kathmandu).timestamp() * 1000)
        expected = int(datetime(2026, 11, 11, 10, tzinfo=kathmandu).timestamp() * 1000)
        self.assertEqual(model.round_down(t, 15, kathmandu), expected)

    def test_legacy_priority(self):
        got = [model.priority_of_legacy(v) for v in (1, 2, 3, 4, 5, None, "", 6, 99, 0, -1)]
        self.assertEqual(got, [5, 4, 3, 2, 1, 0, 0, 1, 1, 0, 0])


if __name__ == "__main__":
    unittest.main()
