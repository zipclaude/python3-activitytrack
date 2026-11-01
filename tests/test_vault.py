# SPDX-License-Identifier: GPL-3.0-or-later
import os
import stat
import tempfile
import unittest
from pathlib import Path

from activitytrack.vault import Vault


class VaultFileTest(unittest.TestCase):
    """Without a secret store: a file only the user reads."""

    def test_a_token_in_a_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            v = Vault(Path(d) / "sub", use_keyring=False)
            self.assertFalse(v.secure)
            self.assertIsNone(v.get("dev1"))
            v.put("dev1", "secret-token")
            self.assertEqual(v.get("dev1"), "secret-token")
            self.assertIsNone(v.get("dev2"))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(v.file.stat().st_mode), 0o600)
            v.forget("dev1")
            self.assertIsNone(v.get("dev1"))
