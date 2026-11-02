# SPDX-License-Identifier: GPL-3.0-or-later
"""
Dates and times as the user prefers them, as on ActivityTrack Web (web
issue #38): European (31/12/2026), American (12/31/2026) or ISO
(2026-12-31) dates; 24 or 12 hours. One choice for every interface: it is
kept in the local copy's state, so the desktop, the command line and the
terminal show the same.
"""

from .i18n import _

EU, US, ISO = "eu", "us", "iso"
H24, H12 = "24", "12"
DATES = (EU, US, ISO)
TIMES = (H24, H12)

_KEY = "formats"
_current = {"date": EU, "time": H24}


def load(store):
    """The choice kept in the local copy (the defaults when none)."""
    saved = store.get_state(_KEY) or {}
    set_formats(saved.get("date", EU), saved.get("time", H24))


def save(store, date_format, time_format):
    set_formats(date_format, time_format)
    store.set_state(_KEY, dict(_current))


def set_formats(date_format, time_format):
    _current["date"] = date_format if date_format in DATES else EU
    _current["time"] = time_format if time_format in TIMES else H24


def date_format():
    return _current["date"]


def time_format():
    return _current["time"]


def day(d, year=True):
    """A date: 31/12/2026, 12/31/2026, 2026-12-31; without the year 31/12, 12/31, 12-31."""
    f = _current["date"]
    if f == ISO:
        return d.isoformat() if year else f"{d.month:02d}-{d.day:02d}"
    if f == US:
        return f"{d.month:02d}/{d.day:02d}" + (f"/{d.year}" if year else "")
    return f"{d.day:02d}/{d.month:02d}" + (f"/{d.year}" if year else "")


def minutes(m):
    """Minutes after midnight as a time of day: 18:30 or 6:30 PM."""
    h, mi = m // 60 % 24, m % 60
    if _current["time"] == H12:
        return f"{(h + 11) % 12 + 1}:{mi:02d} {'AM' if h < 12 else 'PM'}"
    return f"{h:02d}:{mi:02d}"


def time(t):
    """The time of day of a datetime."""
    return minutes(t.hour * 60 + t.minute)


def hour(h):
    """An hour of the time grid: 09, or 9 AM."""
    if _current["time"] == H12:
        return f"{(h + 11) % 12 + 1} {'AM' if h % 24 < 12 else 'PM'}"
    return f"{h:02d}"


def qt_date():
    """The same date format for Qt's date edits."""
    return {EU: "dd/MM/yyyy", US: "MM/dd/yyyy", ISO: "yyyy-MM-dd"}[_current["date"]]


def qt_time():
    return "h:mm AP" if _current["time"] == H12 else "HH:mm"


def placeholder():
    """What a typed date looks like, in the user's order."""
    return {EU: _("gg/mm/aaaa"), US: _("mm/gg/aaaa"), ISO: _("aaaa-mm-gg")}[_current["date"]]


def day_order(a, b):
    """Two numbers of a short date (3/11) as (day, month), in the user's order."""
    return (b, a) if _current["date"] == US else (a, b)
