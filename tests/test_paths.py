# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from activitytrack import paths
from activitytrack.vault import OLD_SERVICE, SERVICE, Vault


@unittest.skipIf(sys.platform == "win32", "XDG folders")
class PathsTest(unittest.TestCase):
    def test_the_shared_folder_and_the_desktops_old_one(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d}):
            old = Path(d, "activitytrack-desktop")
            old.mkdir()
            (old / "activitytrack.sqlite3").write_text("x")
            self.assertEqual(paths.data_dir(), Path(d, "activitytrack"))
            self.assertTrue(Path(d, "activitytrack", "activitytrack.sqlite3").exists())
            self.assertFalse(old.exists())
            # both there: the shared one wins, the old one is left alone
            old.mkdir()
            self.assertEqual(paths.data_dir(), Path(d, "activitytrack"))
            self.assertTrue(old.exists())


class FakeKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, user):
        return self.data.get((service, user))

    def set_password(self, service, user, value):
        self.data[(service, user)] = value

    def delete_password(self, service, user):
        self.data.pop((service, user), None)


class VaultNameTest(unittest.TestCase):
    def test_the_desktops_token_moves_to_the_shared_name(self):
        with tempfile.TemporaryDirectory() as d:
            v = Vault(d, use_keyring=False)
            v.keyring = FakeKeyring()
            v.keyring.set_password(OLD_SERVICE, "dev", "tok")
            self.assertEqual(v.get("dev"), "tok")
            self.assertEqual(v.keyring.data, {(SERVICE, "dev"): "tok"})
