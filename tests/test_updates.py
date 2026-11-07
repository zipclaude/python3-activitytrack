# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import json
import unittest

from activitytrack import updates

BASE = "https://gitlab.com/infraconsulting/activitytrack-desktop/-/releases/"


def release(tag, upcoming=False):
    return {"tag_name": tag, "upcoming_release": upcoming, "_links": {"self": BASE + tag}}


LIST = json.dumps([release("v1.2.0-beta1"), release("v1.1.0"), release("v1.3.0", upcoming=True),
                   release("v1.0.1"), release("v0.9.2")])


class UpdatesTest(unittest.TestCase):
    def test_version_order(self):
        ordered = ["0.9.2", "1.0.0-beta.2", "1.0.0-beta.10", "1.0.0", "1.0.1", "1.1", "1.10.0"]
        self.assertEqual(ordered, sorted(reversed(ordered), key=functools.cmp_to_key(updates.compare)))
        self.assertEqual(0, updates.compare("1.1", "1.1.0"))

    def test_stable_unless_betas(self):
        self.assertEqual("1.1.0", updates.newer(LIST, "1.0.0", betas=False).version)
        beta = updates.newer(LIST, "1.0.0", betas=True)
        self.assertEqual(("1.2.0-beta1", True, BASE + "v1.2.0-beta1"), (beta.version, beta.beta, beta.url))

    def test_upcoming_never_counts_nor_older(self):
        self.assertIsNone(updates.newer(LIST, "1.2.0", betas=True))
        self.assertEqual("1.2.0", updates.newer(json.dumps([release("v1.2.0")]), "1.2.0-beta1", betas=True).version)

    def test_bad_json(self):
        self.assertIsNone(updates.newer("not json", "1.0.0", betas=False))
        self.assertIsNone(updates.newer("{}", "1.0.0", betas=False))
        self.assertEqual("", updates.newer('[{"tag_name": "v2.0.0"}]', "1.0.0", betas=False).url)


if __name__ == "__main__":
    unittest.main()
