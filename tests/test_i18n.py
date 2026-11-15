# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import importlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from activitytrack import i18n, model, pairing, texts
from activitytrack.policy import Policy
from activitytrack.store import Store

PACKAGE = Path(i18n.__file__).resolve().parent

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


# the texts that are the same word in the language as in Italian (the catalogs' keys), and in English
SAME_AS_ITALIAN = {
    "en": {"24 h", "Stop"},
    "de": set(),
    "es": {"24 h", "Cliente", "Incompleta", "Pausa", "dom", "lun", "mar", "Media", "Alta", "Urgente"},
}
SAME_AS_ENGLISH = {"de": {"Pausa", "Tag"}, "es": {"24 h"}}
# the Italian words of the sources that are not interface texts: the words the input accepts, the names
# the old data of the app carries (migration)
NOT_TEXTS = {("app.py", "oggi"), ("migration.py", "Assenza"), ("migration.py", "Ferie"),
             ("migration.py", "Permesso"), ("migration.py", "Malattia")}


def _catalogs():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((PACKAGE / "locale").glob("*.json"))}


def _calls(path, name):
    """The arguments (literals) of the calls of [name] in a source."""
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name:
            yield node.args


class CatalogsTest(unittest.TestCase):
    """The catalogs are complete, as the app's and the web's (activitytrack#23)."""

    def tearDown(self):
        i18n.set_language("it")
        importlib.reload(texts)

    def test_the_languages(self):
        # the app has Italian, English, German and Spanish; Italian is the language of the sources
        self.assertEqual(i18n.LANGUAGES, ("it", "en", "de", "es"))
        self.assertEqual(sorted(_catalogs()), sorted(set(i18n.LANGUAGES) - {"it"}))

    def test_the_catalogs_have_the_same_texts(self):
        catalogs = _catalogs()
        for lang in ("de", "es"):
            self.assertEqual(sorted(catalogs[lang]), sorted(catalogs["en"]), lang)

    def test_nothing_is_left_in_italian_or_english(self):
        catalogs = _catalogs()
        for lang, catalog in catalogs.items():
            same = sorted(k for k, v in catalog.items() if v == k.split("|")[-1])
            self.assertEqual(same, sorted(SAME_AS_ITALIAN[lang]), f"{lang}: left in Italian")
            for k, v in catalog.items():
                self.assertTrue(v.strip(), f"{lang}: {k!r} is empty")
        for lang in ("de", "es"):
            same = sorted(k for k, v in catalogs[lang].items() if v == catalogs["en"][k])
            self.assertEqual(same, sorted(SAME_AS_ENGLISH[lang]), f"{lang}: left in English")

    def test_the_placeholders_are_those_of_the_text(self):
        strings = _strings_module()
        for lang, catalog in _catalogs().items():
            for k, v in catalog.items():
                self.assertEqual(strings.placeholders(v), strings.placeholders(k.split("|")[-1]), f"{lang}: {k!r}")

    @unittest.skipUnless(SCRIPT.exists(), "not in a source tree")
    def test_the_check_finds_what_is_wrong(self):
        strings = _strings_module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "locale").mkdir()
            (root / "m.py").write_text('_("uno %s")\n_("due")\npgettext("c", "tre")\n_("quattro")\n', encoding="utf-8")
            (root / "locale" / "xx.json").write_text(json.dumps({
                "uno %s": "one %d", "due": "", "c|tre": "three", "vecchio": "old"}), encoding="utf-8")
            old, strings.PACKAGE = strings.PACKAGE, root
            try:
                errors = strings.check()
            finally:
                strings.PACKAGE = old
        self.assertEqual(sorted(e.split(": ", 1)[1] for e in errors), sorted([
            "placeholders of 'uno %s'", "empty 'due'", "missing 'quattro'", "stale 'vecchio'"]))

    @unittest.skipUnless(SCRIPT.exists(), "not in a source tree")
    def test_no_text_bypasses_the_catalogs(self):
        # an Italian text of the catalogs written as a plain literal would stay Italian in every language
        keys = {k.split("|")[-1] for k in _catalogs()["en"]}
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            wrapped = {id(n) for call in ast.walk(tree) if isinstance(call, ast.Call)
                       and getattr(call.func, "id", None) in ("_", "pgettext") for n in ast.walk(call)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in keys
                        and id(node) not in wrapped and (path.name, node.value) not in NOT_TEXTS):
                    self.fail(f"{path.name}:{node.lineno}: {node.value!r} is not passed to _()")

    def test_the_activity_model_has_its_words(self):
        # every state, priority, kind of absence and error of the activity model 1.0 has its text
        self.assertEqual(set(texts.DISPLAY_STATES), set(model.DISPLAY_STATES))
        self.assertEqual(set(texts.DISPLAY_SYMBOLS), set(model.DISPLAY_STATES))
        self.assertEqual(set(texts.DISPLAY_COLOURS), set(model.DISPLAY_STATES))
        self.assertEqual(set(texts.STATUSES), {model.TODO, model.RUNNING, model.CLOSED})
        self.assertEqual(set(texts.PRIORITIES), set(model.PRIORITIES))
        self.assertEqual(set(texts.ABSENCE_KINDS), set(model.ABSENCE_KINDS))
        self.assertEqual(set(texts.KINDS), {model.WORK, model.PAUSE, model.TRAVEL, model.ABSENCE})
        # the codes of the errors the commands raise, and of the notices and the refused pairings
        codes = {a[0].value for a in _calls(PACKAGE / "work.py", "WorkError") if isinstance(a[0], ast.Constant)}
        self.assertEqual(sorted(codes - set(texts.ERRORS) - {"required"}), [])
        self.assertEqual(sorted(set(texts.ERRORS) - codes), [])
        source = (PACKAGE / "work.py").read_text(encoding="utf-8") + (PACKAGE / "store.py").read_text(encoding="utf-8")
        for code in texts.NOTICES:
            self.assertIn(f'"{code}"', source, code)
        refused = [v for k, v in vars(pairing).items() if k.isupper() and isinstance(v, str)]
        self.assertEqual(len(refused), 13)
        for code in refused:
            self.assertNotEqual(texts.pair_error(code), code, code)

    def test_the_states_read_in_every_language(self):
        words = {
            "en": ("Deleted", "Absence", "Overdue", "Planned", "To do", "Working", "Travelling", "On pause",
                   "Suspended", "Done", "Incomplete"),
            "de": ("Gelöscht", "Abwesenheit", "Überfällig", "Geplant", "Zu erledigen", "In Arbeit", "Unterwegs",
                   "In Pause", "Unterbrochen", "Erledigt", "Unvollständig"),
            "es": ("Eliminada", "Ausencia", "Con retraso", "Planificada", "Por hacer", "Trabajando", "De viaje",
                   "En pausa", "Suspendida", "Hecha", "Incompleta"),
            "it": ("Eliminata", "Assenza", "In ritardo", "Pianificata", "Da fare", "In lavoro", "In viaggio",
                   "In pausa", "Sospesa", "Fatta", "Incompleta"),
        }
        for lang, expected in words.items():
            i18n.set_language(lang)
            importlib.reload(texts)
            self.assertEqual(tuple(texts.display_label(s) for s in model.DISPLAY_STATES), expected, lang)
        # the same Italian word is the list of the to-dos elsewhere
        i18n.set_language("en")
        importlib.reload(texts)
        self.assertEqual((texts.SHOWS["TODO"], texts.STATUSES[model.TODO]), ("To-dos", "To do"))


class LanguageChoiceTest(unittest.TestCase):
    """The language is the policy's, else the user's choice, else the system's, else English."""

    def tearDown(self):
        i18n.set_language("it")

    def system(self, **env):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.platform", "linux"), \
                mock.patch("locale.getlocale", return_value=(None, None)):
            return i18n.system_language()

    def test_the_language_of_a_locale_name(self):
        for name, language in (("de_AT", "de"), ("de_DE.UTF-8@euro", "de"), ("de-AT", "de"), ("DE", "de"),
                               ("it", "it"), ("en_GB", "en"), ("es_MX.UTF-8", "es"), ("fr_FR", None),
                               ("est", None), ("deu", None), ("C", None), ("POSIX", None), ("", None), (None, None)):
            self.assertEqual(i18n.language_of(name), language, name)

    def test_another_language_or_none_is_english_not_italian(self):
        for code in ("fr_FR", "pt", "est", "", None, "C", "POSIX", 7):
            self.assertEqual(i18n.set_language(code), "en", code)
            self.assertEqual(i18n._("Pausa"), "Pause")

    def test_the_system_language(self):
        for env, language in (
                ({"LANG": "de_AT.UTF-8"}, "de"), ({"LANG": "it_IT"}, "it"), ({"LANG": "es_ES@euro"}, "es"),
                ({"LANG": "en_US.UTF-8"}, "en"), ({"LANG": "fr_FR.UTF-8"}, "en"),
                # C and POSIX are no language, and the first variable set decides
                ({"LANG": "C"}, "en"), ({"LANG": "POSIX"}, "en"), ({"LANG": "C.UTF-8"}, "en"),
                ({"LC_ALL": "C", "LANG": "de_DE.UTF-8"}, "en"), ({"LC_ALL": "it_IT", "LANG": "C"}, "it"),
                ({"LC_MESSAGES": "es_MX", "LANG": "de_DE"}, "es"), ({"LC_ALL": "", "LANG": "de_DE"}, "de"),
                ({}, "en"),
                # LANGUAGE is a list, the first we have; none we have: English; ignored under C
                ({"LANG": "de_DE", "LANGUAGE": "fr:es:de"}, "es"), ({"LANG": "de_DE", "LANGUAGE": "fr"}, "en"),
                ({"LANG": "de_DE", "LANGUAGE": "it_IT:en"}, "it"), ({"LANG": "de_DE", "LANGUAGE": ""}, "de"),
                ({"LANG": "C", "LANGUAGE": "de"}, "en")):
            self.assertEqual(self.system(**env), language, env)

    def test_the_default_locale_when_the_environment_says_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("sys.platform", "linux"), \
                mock.patch("locale.getlocale", return_value=("de_DE", "UTF-8")):
            self.assertEqual(i18n.system_language(), "de")

    def test_the_choice_without_a_store(self):
        self.assertEqual(i18n.resolve_language(system="de_AT"), "de")
        self.assertEqual(i18n.resolve_language(system="fr_FR"), "en")
        self.assertEqual(i18n.resolve_language(system="C"), "en")
        with mock.patch("activitytrack.i18n.system_language", return_value="es"):
            self.assertEqual(i18n.resolve_language(), "es")

    def test_the_policy_wins_over_the_choice_that_wins_over_the_system(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "a.sqlite3")

            def resolved(policy=None, choice=None, system="de_DE"):
                ui = {"language": policy} if policy is not None else {}
                store.set_state("policy", json.dumps({"format": "activitytrack-policy", "schema": 1, "ui": ui}))
                store.set_state(i18n.PREFERENCE_KEY, choice)
                return i18n.resolve_language(store, system=system)

            self.assertEqual(resolved(), "de")
            self.assertEqual(resolved(choice="es"), "es")
            self.assertEqual(resolved(policy="it", choice="es"), "it")
            self.assertEqual(resolved(policy="en", choice="es", system="it_IT"), "en")
            # a value that is not a language of ours is ignored at its step
            self.assertEqual(resolved(policy="fr", choice="es"), "es")
            self.assertEqual(resolved(policy="de_AT", choice="es"), "es")
            self.assertEqual(resolved(policy="fr", choice="xx"), "de")
            self.assertEqual(resolved(policy="fr", choice="xx", system="fr_FR"), "en")
            self.assertEqual(resolved(policy=["it"], choice=3, system="C"), "en")
            # no policy received at all
            store.set_state("policy", None)
            self.assertEqual(i18n.resolve_language(store, system="es_ES"), "es")
            store.close()
