# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from activitytrack import app as app_module, formats, texts, timegrid
from activitytrack.store import Store


class FormatsTest(unittest.TestCase):
    def tearDown(self):
        formats.set_formats(formats.EU, formats.H24)

    def test_the_three_dates_and_two_clocks(self):
        d = date(2026, 12, 31)
        self.assertEqual(formats.day(d), "31/12/2026")
        self.assertEqual(formats.minutes(18 * 60 + 30), "18:30")
        formats.set_formats(formats.US, formats.H12)
        self.assertEqual((formats.day(d), formats.day(d, year=False)), ("12/31/2026", "12/31"))
        self.assertEqual((formats.minutes(18 * 60 + 30), formats.minutes(0), formats.minutes(12 * 60)), ("6:30 PM", "12:00 AM", "12:00 PM"))
        self.assertEqual((formats.hour(0), formats.hour(13)), ("12 AM", "1 PM"))
        self.assertEqual((formats.qt_date(), formats.qt_time()), ("MM/dd/yyyy", "h:mm AP"))
        formats.set_formats(formats.ISO, "xx")
        self.assertEqual((formats.day(d), formats.time_format()), ("2026-12-31", formats.H24))

    def test_kept_in_the_local_copy_for_every_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "data.sqlite3")
            formats.load(store)
            self.assertEqual(formats.date_format(), formats.EU)
            formats.save(store, formats.US, formats.H12)
            formats.set_formats(formats.EU, formats.H24)
            formats.load(store)
            self.assertEqual((formats.date_format(), formats.time_format()), (formats.US, formats.H12))
            store.close()

    def test_the_app_loads_and_refreshes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = app_module.App(tmp)
            self.assertEqual(formats.date_format(), formats.EU)
            other = Store(Path(tmp) / "activitytrack.sqlite3")
            formats.save(other, formats.ISO, formats.H12)
            formats.set_formats(formats.EU, formats.H24)
            a.refresh_policy()
            self.assertEqual((formats.date_format(), formats.time_format()), (formats.ISO, formats.H12))
            other.close()
            a.store.close()

    def test_texts_and_parsers_follow_the_choice(self):
        formats.set_formats(formats.US, formats.H12)
        today = date(2026, 11, 2)
        self.assertEqual(app_module.parse_day("11/3", today), date(2026, 11, 3))
        self.assertEqual(app_module.parse_minute("6:30 pm"), 18 * 60 + 30)
        self.assertEqual(app_module.parse_minute("12am"), 0)
        with self.assertRaises(app_module.InputError):
            app_module.parse_minute("13 pm")
        when = app_module.parse_when("11/3 9:30 PM", today)
        self.assertEqual(datetime.fromtimestamp(when / 1000).strftime("%Y-%m-%d %H:%M"), "2026-11-03 21:30")
        now = datetime.now()
        self.assertTrue(texts.clock(int(now.timestamp() * 1000)).endswith(("AM", "PM")))
        formats.set_formats(formats.EU, formats.H24)
        self.assertEqual(app_module.parse_day("3/11", today), date(2026, 11, 3))


class TimeGridTest(unittest.TestCase):
    def test_cells_and_window(self):
        self.assertEqual(timegrid.span(111, 127), (555, 640))      # 09:15-10:40, as the app
        self.assertEqual(timegrid.span(127, 111), (555, 640))
        self.assertEqual(timegrid.window_for(9 * 60), 7)
        self.assertEqual(timegrid.window_for(23 * 60), 12)
        self.assertEqual(timegrid.window_for_work(8 * 60, 18 * 60), 7)
        self.assertEqual(timegrid.shift(11, 3), 12)

    def test_busy_minutes_clipped_merged_and_skipped(self):
        tz = timezone.utc
        day = date(2026, 11, 2)
        at = lambda h, m=0: int(datetime(2026, 11, 2, h, m, tzinfo=tz).timestamp() * 1000)  # noqa: E731
        segs = [
            {"uid": "a", "startUtcMs": at(9), "endUtcMs": at(10, 30)},
            {"uid": "b", "startUtcMs": at(10), "endUtcMs": at(11)},
            {"uid": "c", "startUtcMs": at(14), "endUtcMs": None},
            {"uid": "d", "startUtcMs": at(9) - int(timedelta(days=1).total_seconds() * 1000), "endUtcMs": at(0, 30)},
        ]
        self.assertEqual(timegrid.busy_minutes(segs, day, tz, at(15)), [(0, 30), (540, 660), (840, 900)])
        self.assertEqual(timegrid.busy_minutes(segs, day, tz, at(15), skip="b"), [(0, 30), (540, 630), (840, 900)])
