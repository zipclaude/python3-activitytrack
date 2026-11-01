# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import unittest

from activitytrack.pairing import BAD_PIN, NOT_A_LINK, NOT_HTTPS, VERSION, PairError, PairLink

PIN = base64.b64encode(bytes(32)).decode()


class PairLinkTest(unittest.TestCase):
    def test_a_link_of_the_web(self):
        link = PairLink.parse("activitytrack://pair?v=1&u=https%3A%2F%2Fat.example.com%2Fat%2F&c=abc-123")
        self.assertEqual(link, PairLink("https://at.example.com/at", "abc-123", None))

    def test_the_pin_of_a_self_signed_server(self):
        link = PairLink.parse(f"  activitytrack://pair?v=1&u=https%3A%2F%2F10.0.0.5&c=x&f={PIN.replace('+', '%2B')}\n")
        self.assertEqual(link.pin, PIN)

    def assertRefused(self, text, code, **kw):
        with self.assertRaises(PairError) as e:
            PairLink.parse(text, **kw)
        self.assertEqual(e.exception.code, code)

    def test_what_is_refused(self):
        self.assertRefused("https://at.example.com/pair?c=x", NOT_A_LINK)
        self.assertRefused("activitytrack://pair?v=2&u=https%3A%2F%2Fa.b&c=x", VERSION)
        self.assertRefused("activitytrack://pair?v=1&u=https%3A%2F%2Fa.b", NOT_A_LINK)
        self.assertRefused("activitytrack://pair?v=1&u=http%3A%2F%2Fa.b&c=x", NOT_HTTPS)
        self.assertRefused("activitytrack://pair?v=1&u=https%3A%2F%2Fa.b&c=x&f=abc", BAD_PIN)

    def test_http_only_for_development(self):
        link = PairLink.parse("activitytrack://pair?v=1&u=http%3A%2F%2F127.0.0.1%3A8000&c=x", allow_http=True)
        self.assertEqual(link.server, "http://127.0.0.1:8000")
