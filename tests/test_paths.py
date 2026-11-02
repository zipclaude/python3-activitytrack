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

    def test_two_interfaces_move_the_old_folder_at_once(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d}):
            old, new = Path(d, "activitytrack-desktop"), Path(d, "activitytrack")
            old.mkdir()
            rename = Path.rename

            def first_the_other(self, target):
                # the other interface moves it between the check and this rename
                rename(self, target)
                return rename(self, target)
            with mock.patch.object(Path, "rename", first_the_other):
                self.assertEqual(paths.data_dir(), new)
            self.assertTrue(new.is_dir())


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

    def test_a_failing_secret_store_is_tried_again(self):
        with tempfile.TemporaryDirectory() as d:
            v = Vault(d, use_keyring=False)
            v.keyring = FakeKeyring()
            set_password = v.keyring.set_password
            v.keyring.set_password = mock.Mock(side_effect=RuntimeError("locked"))
            with self.assertLogs("activitytrack.vault", "WARNING"):
                v.put("dev", "tok")
            # kept in the file, and said so
            self.assertFalse(v.secure)
            self.assertEqual(v.get("dev"), "tok")
            v.keyring.set_password = set_password
            v.put("dev", "tok2")
            self.assertTrue(v.secure)
            self.assertEqual(v.keyring.data, {(SERVICE, "dev"): "tok2"})
            self.assertEqual(v.get("dev"), "tok2")
