# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import unittest
from pathlib import Path

from activitytrack import i18n
from activitytrack.policy import Policy


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "strings.py"


def _strings_module():
    path = SCRIPT
    spec = importlib.util.spec_from_file_location("strings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class I18nTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("it")

    # a check of the sources: the packages' job runs the tests on the installed code, without them
    @unittest.skipUnless(SCRIPT.exists(), "not in a source tree")
    def test_every_text_is_translated(self):
        self.assertEqual(_strings_module().check(), [])

    def test_languages(self):
        self.assertEqual(i18n.set_language("de_AT"), "de")
        self.assertEqual(i18n._("Lavoro"), "Arbeit")
        self.assertEqual(i18n._("Campo obbligatorio: %s.") % "Rolle", "Pflichtfeld: Rolle.")
        # another language: English
        self.assertEqual(i18n.set_language("fr_FR"), "en")
        self.assertEqual(i18n._("Pausa"), "Pause")
        # an unknown text stays as it is
        self.assertEqual(i18n._("— nuovo —"), "— nuovo —")
        self.assertEqual(i18n.set_language("it"), "it")
        self.assertEqual(i18n._("Viaggio"), "Viaggio")

    def test_an_interface_adds_its_catalogs(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "de.json").write_text(json.dumps({"Esci": "Beenden"}), encoding="utf-8")
            i18n.add_catalogs(d)
            i18n.set_language("de")
            self.assertEqual((i18n._("Esci"), i18n._("Lavoro")), ("Beenden", "Arbeit"))
            i18n._folders.remove(Path(d))

    def test_the_policy_forces_it(self):
        p = Policy({"format": "activitytrack-policy", "schema": 1, "ui": {"language": "es"}})
        self.assertEqual(p.language, "es")
        self.assertIsNone(Policy({"format": "activitytrack-policy", "schema": 1, "ui": {"language": "fr"}}).language)
