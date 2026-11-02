# SPDX-License-Identifier: GPL-3.0-or-later
"""
The application as every interface sees it: the shared data folder and
pairing, the rules of the library, the sync, finding records by the short
id the listings show, what the views show (as dicts, ready for JSON) and
the dates and times typed by the user.
"""

import re
from datetime import date, datetime, time, timedelta

from . import formats, texts
from .i18n import _
from .policy import stored
from .session import Session
from .sync import Runner
from .todos import Todos, state as todo_state
from .work import ACTIVE, Work, WorkError, local_zone

ID_LEN = 6


class InputError(Exception):
    """A request of the user that cannot be done as typed (an unknown id, a wrong date)."""


class App:
    def __init__(self, folder, dev=False):
        self.session = Session(folder, allow_http=dev)
        self.work = Work(self.session.store, stored(self.session.store), zone=local_zone())
        self.todos = Todos(self.work)
        self.runner = Runner(self.session)

    @property
    def store(self):
        return self.session.store

    @property
    def paired(self):
        return self.session.account is not None

    def refresh_policy(self):
        self.work.policy = stored(self.store)

    def sync(self, full=True):
        """(state, detail) of one run; the policy may have changed."""
        state, detail = self.runner.run(full)
        self.refresh_policy()
        return state, detail

    # ---- finding records by the short id the listings show ----

    def find(self, type_, ref):
        ref = (ref or "").strip().lower()
        if len(ref) < 3:
            raise InputError(_("Indica almeno 3 caratteri dell'identificativo."))
        found = [r for r in self.store.all(type_) if r["uid"].lower().startswith(ref)]
        if not found:
            raise InputError(_("Nessun elemento con identificativo «%s».") % ref)
        if len(found) > 1:
            raise InputError(_("Identificativo «%s» ambiguo: scrivine qualche carattere in più.") % ref)
        return found[0]

    def running_one(self, ref=None, active_only=False):
        """The activity [ref], or the only one running."""
        if ref:
            return self.find("entry", ref)
        running = [e for e in self.work.running() if not active_only or e.get("status") == ACTIVE]
        if len(running) == 1:
            return running[0]
        if not running:
            raise InputError(_("Nessuna attività in corso."))
        raise InputError(_("Più attività in corso: indica quale."))

    # ---- what the commands show ----

    def entry_info(self, e):
        w = self.work
        totals = w.totals(e["uid"])
        seg = w.open_segment(e["uid"])
        since = seg["startUtcMs"] if seg else (w.last_boundary(e["uid"]) or None)
        return {
            "id": e["uid"][:ID_LEN], "uid": e["uid"], "status": e.get("status"), "mode": e.get("mode"),
            "state": texts.state_label(e), "client": w.name("client", e.get("clientUid")),
            "company": w.name("company", e.get("companyUid")), "activity": e.get("activity") or "",
            "tags": e.get("tags") or [], "since": _iso(since), "needs_review": bool(e.get("needsReview")),
            "work_min": totals["WORK"] // 60_000, "travel_min": totals["TRAVEL"] // 60_000,
            "pause_min": totals["PAUSE"] // 60_000, "label": one_line(w.label(e)),
        }

    def todo_info(self, t):
        tz = self.todos.tz()
        st = todo_state(t, datetime.now(tz), tz)
        w = self.work
        return {
            "id": t["uid"][:ID_LEN], "uid": t["uid"], "title": t.get("title") or "", "state": st,
            "state_label": texts.TODO_STATES.get(st, st), "client": w.name("client", t.get("clientUid")),
            "company": w.name("company", t.get("companyUid")), "when": texts.todo_when(t, tz),
            "plan_date": t.get("planDate"), "plan_start_min": t.get("planStartMin"),
            "duration_min": t.get("durationMin"), "due_date": t.get("dueDate"), "priority": t.get("priority"),
        }

    def sync_info(self, state="idle", detail=""):
        return {"state": state, "text": texts.sync_state(self.session, state, detail),
                "attention": texts.sync_needs_attention(self.session, state),
                "pending": len(self.store.pending(100_000)), "last_sync": _iso(self.store.get_state("last_sync"))}


def one_line(text):
    """A description of several lines (written on the desktop) on one line of the terminal."""
    return " ".join((text or "").split())


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="minutes") if ms else None


# ---- times and dates typed by the user ----

# in the four languages of the interface
_DAY_WORDS = {
    "oggi": 0, "today": 0, "heute": 0, "hoy": 0,
    "domani": 1, "tomorrow": 1, "morgen": 1, "mañana": 1, "manana": 1,
    "ieri": -1, "yesterday": -1, "gestern": -1, "ayer": -1,
}


def parse_day(text, today=None):
    """"2026-11-03", "3/11", "3/11/2026", "oggi", "domani", "ieri"."""
    today = today or date.today()
    t = (text or "").strip().lower()
    if t in _DAY_WORDS:
        return today + timedelta(days=_DAY_WORDS[t])
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        d, mo = formats.day_order(int(m.group(1)), int(m.group(2)))
        try:
            return date(year, mo, d)
        except ValueError:
            pass
    raise InputError(_("Data non valida: «%s» (per esempio 2026-11-03, 3/11, oggi, domani).") % text)


def parse_minute(text):
    """"9:30", "09.30", "930", "9:30 pm" → minutes after midnight."""
    t = (text or "").strip().lower()
    half = re.search(r"\s*([ap])\.?m?\.?$", t)
    if half:
        t = t[:half.start()]
    m = re.fullmatch(r"(\d{1,2})(?:[:.]?(\d{2}))?", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if half and 1 <= h <= 12:
            h = h % 12 + (12 if half.group(1) == "p" else 0)
        elif half:
            h = 24  # 13 pm: not a time
        if h < 24 and mi < 60:
            return h * 60 + mi
    raise InputError(_("Orario non valido: «%s» (per esempio 9:30).") % text)


def parse_when(text, today=None):
    """"9:30" (today) or "3/11 9:30" or "2026-11-03 9:30 pm" → epoch ms."""
    parts = (text or "").split()
    if len(parts) > 1 and parts[-1].lower().strip(".") in ("am", "pm", "a", "p"):
        parts[-2:] = [" ".join(parts[-2:])]
    day = parse_day(parts[0], today) if len(parts) == 2 else (today or date.today())
    minute = parse_minute(parts[-1])
    moment = datetime.combine(day, time(minute // 60, minute % 60)).astimezone()
    return int(moment.timestamp() * 1000)


def describe_error(e):
    if isinstance(e, WorkError):
        return texts.error_text(e)
    return str(e)
