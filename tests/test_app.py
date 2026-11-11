# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from datetime import date

from activitytrack import app as app_module
from activitytrack.app import App, InputError
from activitytrack.work import StartRequest


class ParseTest(unittest.TestCase):
    def test_dates_and_times(self):
        today = date(2026, 11, 2)
        self.assertEqual(app_module.parse_day("domani", today), date(2026, 11, 3))
        self.assertEqual(app_module.parse_day("morgen", today), date(2026, 11, 3))
        self.assertEqual(app_module.parse_day("3/11", today), date(2026, 11, 3))
        self.assertEqual(app_module.parse_day("03.11.27", today), date(2027, 11, 3))
        self.assertEqual(app_module.parse_day("2026-12-01", today), date(2026, 12, 1))
        self.assertEqual(app_module.parse_minute("9:30"), 570)
        self.assertEqual(app_module.parse_minute("0930"), 570)
        for bad in ("25:00", "9:75", "x"):
            with self.assertRaises(InputError):
                app_module.parse_minute(bad)
        with self.assertRaises(InputError):
            app_module.parse_day("31/02", today)
        self.assertEqual(app_module.one_line("Riga 1\nRiga 2 "), "Riga 1 Riga 2")


class AppTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.app = App(self.dir.name)

    def tearDown(self):
        self.app.store.close()
        self.dir.cleanup()

    def test_find_by_the_short_id_and_the_infos(self):
        a = self.app.work.start(StartRequest(client="Acme", activity="Rete\nWi-Fi"))
        info = self.app.entry_info(self.app.work.entry(a))
        self.assertEqual((info["id"], info["label"], info["mode"]), (a[:6], "Acme · Rete Wi-Fi", "WORK"))
        self.assertEqual(self.app.find("entry", a[:4])["uid"], a)
        self.assertEqual(self.app.running_one()["uid"], a)
        for ref in ("ab", "zzzzzz"):
            with self.assertRaises(InputError):
                self.app.find("entry", ref)
        self.assertEqual(self.app.sync_info()["state"], "idle")

    def test_infos_of_the_activity_model(self):
        w = self.app.work
        t = w.save_todo(None, "Chiamare", client="Acme", priority=3)
        info = self.app.todo_info(w.entry(t))
        self.assertEqual((info["title"], info["state"], info["priority"]), ("Chiamare", "open", 3))
        w.start_todo(t)
        info = self.app.entry_info(w.entry(t))
        self.assertEqual((info["display"], info["state"], info["kind"], info["priority"]),
                         ("WORK", "In lavoro", "ACTIVITY", 3))
        self.assertIsNotNone(info["since"])
        w.stop(t)
        self.assertEqual(self.app.entry_info(w.entry(t))["display"], "DONE")
        a = w.add_absence(date(2026, 11, 12), start_min=600, end_min=660, absence_kind="LEAVE")
        info = self.app.entry_info(w.entry(a))
        self.assertEqual((info["display"], info["absence_kind"], info["absence_min"]), ("ABSENCE", "LEAVE", 60))
        with self.assertRaises(InputError):
            self.app.running_one()
