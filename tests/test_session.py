# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from unittest import mock

from activitytrack import session as session_module
from activitytrack.pairing import Account, PairLink
from activitytrack.session import Session


def account(server="https://at.example.com", username="anna", device="d1"):
    return Account(server=server, pin=None, device_id=device, username=username, name="Anna", profile="user", org="X")


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.s = Session(self.dir.name, use_keyring=False)

    def tearDown(self):
        self.s.store.close()
        self.dir.cleanup()

    def paired(self, acc, token="tok"):
        with mock.patch.object(session_module, "pair", return_value=(acc, token)):
            return self.s.pair(PairLink(acc.server, "code", None))

    def test_pairing_keeps_account_and_token(self):
        self.assertIsNone(self.s.http())
        self.paired(account())
        self.assertEqual(self.s.account.username, "anna")
        self.assertEqual(self.s.http().token, "tok")
        self.assertEqual(self.s.http().base, "/api/v1/")

    def test_pairing_again_with_the_same_user_keeps_the_copy(self):
        self.paired(account())
        self.s.store.write("todo", "t1", {"title": "x"}, now_ms=1)
        self.s.store.set_state("revoked", True)
        self.paired(account(device="d2"), token="tok2")
        self.assertIsNotNone(self.s.store.get("todo", "t1"))
        self.assertFalse(self.s.store.get_state("revoked"))
        self.assertEqual(self.s.http().token, "tok2")
        self.assertIsNone(self.s.vault.get("d1"))

    def test_another_server_or_user_starts_empty(self):
        self.paired(account())
        self.s.store.write("todo", "t1", {"title": "x"}, now_ms=1)
        self.paired(account(username="bruno", device="d2"))
        self.assertIsNone(self.s.store.get("todo", "t1"))

    def test_unpairing_forgets_the_server_not_the_data(self):
        self.paired(account())
        self.s.store.write("todo", "t1", {"title": "x"}, now_ms=1)
        self.s.unpair()
        self.assertIsNone(self.s.account)
        self.assertIsNone(self.s.http())
        self.assertIsNone(self.s.vault.get("d1"))
        self.assertIsNotNone(self.s.store.get("todo", "t1"))
