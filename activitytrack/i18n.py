# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Flavio Visentin <flavio-activitytrack@infraconsulting.it>
"""
The texts are written in Italian, as the app's and the web's; `_` gives
them in the language chosen at start. Each package keeps its catalogs in
a locale folder (<language>.json: Italian text → translation): the
library's, and the ones an interface adds with add_catalogs() for its own
texts. The language is the policy's ui.language (English when it is there
but is no language of ours), else the user's choice (where the interface
has a setting), else the system's (resolve_language); another language
than it, en, de, es gets English, and so does a system that says none.
Interfaces that keep texts in constants set it before importing their modules.
"""

import json
import re
from collections import namedtuple
from pathlib import Path

LANGUAGES = ("it", "en", "de", "es")
# the local copy's state that keeps the user's choice (None: the system's), written by the interfaces
# that have a setting for it
PREFERENCE_KEY = "language"
_folders = [Path(__file__).with_name("locale")]
_catalog = {}
_language = "it"


def add_catalogs(folder):
    """An interface's locale folder, loaded with the library's (now and at every set_language)."""
    folder = Path(folder)
    if folder not in _folders:
        _folders.append(folder)
        _load()


def language_of(name):
    """
    The language of a locale name ("de_AT", "de_DE.UTF-8@euro", "de-AT", "DE"): "it", "en", "de" or "es",
    None when it is another one ("fr_FR", "est", "C", "POSIX", "", not a text): English is for the caller
    to choose.
    """
    match = re.match(r"[A-Za-z]+", name) if isinstance(name, str) else None
    code = match.group().lower() if match else ""
    return code if code in LANGUAGES else None


def set_language(code):
    """[code]: "it", "en", "de", "es", or a locale ("de_AT"); returns the language used (English for any
    other one, and for none)."""
    global _language
    _language = language_of(code) or "en"
    _load()
    return _language


PolicyLanguage = namedtuple("PolicyLanguage", "code invalid")


def policy_language(store):
    """
    What the policy says of the language, for the interfaces that show it: PolicyLanguage(code, invalid).
    (None, False): the policy does not say, the user's choice and the system's apply. ("de", False): the
    policy forces that language. (None, True): ui.language is there but is not one of LANGUAGES: the
    language is forced to English (texts.language_note says so). The rest of the policy is not affected.
    """
    from .policy import stored
    policy = stored(store)
    return PolicyLanguage(policy.language, policy.language_invalid)


def resolve_language(store=None, system=None):
    """
    The interface's language: the policy's ui.language (a value that is not a language: English, forced),
    else the user's choice (the store's state PREFERENCE_KEY, ignored when it is not a language), else the
    system's, else English. [store]: the local copy (None: only the system's); [system]: the system's
    language when the caller knows it better (the Qt locale), else system_language().
    """
    chosen = None
    if store is not None:
        code, invalid = policy_language(store)
        chosen = "en" if invalid else code or language_of(store.get_state(PREFERENCE_KEY))
    return chosen or language_of(system if system is not None else system_language()) or "en"


def _load():
    _catalog.clear()
    if _language == "it":
        return
    for folder in _folders:
        try:
            _catalog.update(json.loads((folder / f"{_language}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass


def language():
    return _language


def system_language():
    """
    The system's language, without Qt (the command line's interfaces): one of LANGUAGES, "en" for any
    other one and for none. On Windows the display language; elsewhere what gettext does: LANGUAGE (a
    list, "fr:de": the first we have, else English) when the locale is not C or POSIX; else the first
    of LC_ALL, LC_MESSAGES and LANG that is set (an empty one does not count), else Python's default
    locale.
    """
    import locale
    import os
    import sys
    if sys.platform == "win32":
        # the display language ("de_DE"); getlocale() would give names as "German_Germany"
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return language_of(locale.windows_locale.get(lcid)) or "en"
        except (AttributeError, OSError):
            return "en"
    name = next((v for v in (os.environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG")) if v), None)
    if name is None:
        try:
            name = locale.getlocale()[0]
        except ValueError:
            name = None
    if not name or re.match(r"(C|POSIX)($|[.@])", name):
        # no language, or the one of the programmers: gettext ignores LANGUAGE then, and so do we
        return "en"
    listed = [c for c in os.environ.get("LANGUAGE", "").split(":") if c]
    if listed:
        # the user's list: its first language we have, else English, not the locale's
        return next((language_of(c) for c in listed if language_of(c)), "en")
    return language_of(name) or "en"


def _(text):
    return _catalog.get(text) or text


def pgettext(context, text):
    """
    [text] said in a [context] when the same Italian word has another translation there ("Da fare" is
    "To-dos" as a list and "To do" as the state of an activity). The catalogs hold it as "context|text";
    without that key it is the plain translation of [text].
    """
    return _catalog.get(f"{context}|{text}") or _catalog.get(text) or text
