# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from activitytrack.store import Store


class StoreTest(unittest.TestCase):
    def test_close_closes_every_threads_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "d.sqlite3")
            other = []
            t = threading.Thread(target=lambda: other.append(store.db))
            t.start()
            t.join()
            store.close()
            with self.assertRaises(sqlite3.ProgrammingError):
                other[0].execute("SELECT 1")

    def test_a_file_that_is_not_a_database_leaves_nothing_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.sqlite3"
            path.write_bytes(b"garbage!" * 100)
            with self.assertRaises(sqlite3.DatabaseError):
                Store(path)
