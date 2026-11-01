# SPDX-License-Identifier: GPL-3.0-or-later
"""
The texts are written in Italian, as the app's and the web's; `_` gives
them in the language chosen at start. Each package keeps its catalogs in
a locale folder (<language>.json: Italian text → translation): the
library's, and the ones an interface adds with add_catalogs() for its own
texts. The language is the policy's ui.language, else the system's;
another language than it, en, de, es gets English. Interfaces that keep
texts in constants set it before importing their modules.
"""

import json
from pathlib import Path

LANGUAGES = ("it", "en", "de", "es")
_folders = [Path(__file__).with_name("locale")]
_catalog = {}
_language = "it"


def add_catalogs(folder):
    """An interface's locale folder, loaded with the library's (now and at every set_language)."""
    folder = Path(folder)
    if folder not in _folders:
        _folders.append(folder)
        _load()


def set_language(code):
    """[code]: "it", "en", "de", "es", or a locale ("de_AT"); returns the language used."""
    global _language
    code = (code or "it")[:2].lower()
    _language = code if code in LANGUAGES else "en"
    _load()
    return _language


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
    """The system's language, without Qt (the command line's interfaces)."""
    import locale
    import os
    import sys
    if sys.platform == "win32":
        # the display language ("de_DE"); getlocale() would give names as "German_Germany"
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return locale.windows_locale.get(lcid, "en")[:2]
        except (AttributeError, OSError):
            return "en"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var)
        if value and value not in ("C", "POSIX"):
            return value.split(":")[0][:2]
    try:
        value = locale.getlocale()[0]
    except ValueError:
        value = None
    return (value or "it")[:2]


def _(text):
    return _catalog.get(text) or text
