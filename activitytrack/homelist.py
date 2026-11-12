# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Home list of the interfaces, over the activity model 1.0 (app ui/home/HomeTabs.kt homeSections; the
web's port is server web/homelist.py): the TODO activities, the tasks ticked off, the deleted ones and
the closed activities, in the sections and with the filters of the app. The plan, the due date and the
displayed state are in model.py, the commands in work.py.
"""

from datetime import datetime, timedelta

from . import model
from .model import at_minute as _at, moment
from .work import CLOSED

# period of the Home list: -1 today, 0 everything, else hours (setting homeWindow)
WINDOWS = (-1, 24, 72, 168, 720, 0)
TODO, MISSING, DONE, IGNORED = "TODO", "MISSING", "DONE", "IGNORED"
SHOWS = (TODO, MISSING, DONE, IGNORED)
DEFAULT_SHOW = (TODO, MISSING)
SECTIONS = ("overdue", "missing", "today", "next", "earlier", "no_date", "ignored")


def sections(todos, stopped, show, window, company=None, tag=None, carry_over_min=360, now=None, tz=None,
             working=frozenset(), planned=None):
    """
    [(section, [item])], empty sections left out (as the app's Home). todos: TODO activities, the tasks
    ticked off (CLOSED without segments) and the deleted ones; stopped: [(activity, end)] of the closed
    ones with segments (`_display` INCOMPLETE puts one in "missing"); working: uids not shown; planned:
    {uid: planned segments} of the TODO activities (they rule the lateness, §2.3). An item is ("todo",
    activity, displayed state: TODO, PLANNED, LATE, DONE or DELETED) or ("entry", activity, end).
    """
    now = now or datetime.now(tz)
    today = now.astimezone(tz).date()
    start_today = _at(today, carry_over_min, tz)
    if start_today > now:
        start_today = _at(today - timedelta(days=1), carry_over_min, tz)
    if window == -1:
        ahead = _at(today + timedelta(days=1), 0, tz)
    else:
        ahead = None if window == 0 else now + timedelta(hours=window)
    behind = start_today if window == -1 else None if window == 0 else now - timedelta(hours=window)
    groups = {s: [] for s in SECTIONS}
    far = datetime.max.replace(tzinfo=tz)

    def ours(company_uid):
        return company is None or company_uid == company

    def ms(dt):
        return dt.timestamp() * 1000

    for t in todos if not tag else ():
        if not ours(t.get("companyUid")):
            continue
        st = model.display(t, (planned or {}).get(t["uid"], ()), now, tz)[0]
        if st in (model.DONE, model.DELETED):
            at_ms = t.get("deletedUtcMs") if st == model.DELETED else t.get("closedUtcMs")
            at_ms = at_ms or t.get("updatedUtcMs") or 0
            at = datetime.fromtimestamp(at_ms / 1000, tz)
            if st == model.DONE and DONE in show and (behind is None or at >= behind):
                key = ("1", -at_ms) if at >= start_today else (-at_ms,)
                groups["today" if at >= start_today else "earlier"].append((key, ("todo", t, st)))
            elif st == model.DELETED and IGNORED in show and (behind is None or at >= behind):
                groups["ignored"].append(((-at_ms,), ("todo", t, st)))
            continue
        if TODO not in show or t["uid"] in working:
            continue
        m = moment(t, tz)
        if st == model.LATE:
            groups["overdue"].append(((ms(m or far),), ("todo", t, st)))
        elif m is None:
            # the highest priority first (5 emergency ... 0 none)
            groups["no_date"].append(((-(t.get("priority") or 0), (t.get("activity") or "").lower()),
                                      ("todo", t, st)))
        elif ahead is not None and m > ahead:
            continue
        elif m.astimezone(tz).date() > today:
            groups["next"].append(((ms(m),), ("todo", t, st)))
        else:
            groups["today"].append((("0", ms(m)), ("todo", t, st)))
    for e, end in stopped:
        if not ours(e.get("companyUid")) or (tag and tag not in (e.get("tags") or [])):
            continue
        end_ms = ms(end)
        if e.get("kind") == model.ABSENCE:
            # by its days: ahead "next", covering today "today", over "earlier" (shown with the to-dos
            # while it is not over, else with the done ones)
            first = datetime.fromtimestamp((e.get("_startMs") or end_ms) / 1000, tz)
            live = end >= start_today
            if (TODO if live else DONE) not in show or (ahead is not None and first > ahead) \
                    or (not live and behind is not None and end < behind):
                continue
            if first.astimezone(tz).date() > today:
                groups["next"].append(((ms(first),), ("entry", e, end)))
            elif live:
                groups["today"].append((("0", ms(first)), ("entry", e, end)))
            else:
                groups["earlier"].append(((-end_ms,), ("entry", e, end)))
            continue
        if e.get("_display") == model.INCOMPLETE:
            if MISSING in show:
                groups["missing"].append(((-end_ms,), ("entry", e, end)))
        elif DONE in show and (behind is None or end >= behind):
            # after today's to-dos, the most recent first
            key = ("1", -end_ms) if end >= start_today else (-end_ms,)
            groups["today" if end >= start_today else "earlier"].append((key, ("entry", e, end)))
    out = []
    for s in SECTIONS:
        rows = groups[s]
        if rows:
            rows.sort(key=lambda r: r[0])
            out.append((s, [r[1] for r in rows]))
    return out


def all_todos(work):
    """The TODO activities, the tasks ticked off and the deleted ones (the items of sections)."""
    out = []
    for e in work.entries(None):
        if e.get("kind") == model.ABSENCE:
            continue
        if e.get("status") == model.TODO or (e.get("status") == CLOSED and not work.real_segments(e["uid"])):
            out.append(e)
    return out


def planned_of(work, todos):
    """{uid: planned segments} of the TODO activities that have some."""
    uids = {t["uid"] for t in todos if t.get("status") == model.TODO}
    out = {}
    for s in work.store.all("segment"):
        if s.get("entryUid") in uids and model.is_planned(s):
            out.setdefault(s["entryUid"], []).append(s)
    return out


def home(work, show=DEFAULT_SHOW, window=-1, company=None, tag=None, now=None):
    """The Home list (below the activities running)."""
    tz = work.tz
    todos = all_todos(work) if work.policy.feature("todos") else []
    stopped = []
    for e in work.entries():
        if e.get("status") == CLOSED and work.real_segments(e["uid"]):
            end = work.last_boundary(e["uid"])
            shown = dict(e, _display=work.display(e)[0])
            if e.get("kind") == model.ABSENCE:
                shown["_startMs"] = min(s["startUtcMs"] for s in work.real_segments(e["uid"]))
            stopped.append((shown, datetime.fromtimestamp((end or e.get("updatedUtcMs") or 0) / 1000, tz)))
    carry = work.policy.setting("carryOverMin", 360)
    return sections(todos, stopped, set(show), window, company, tag, carry, now or datetime.now(tz), tz,
                    planned=planned_of(work, todos))


def startable(work, limit=10, now=None):
    """Overdue and today's open TODO activities, for the tray menu."""
    now = now or datetime.now(work.tz)
    out = [it for s, items in home(work, (TODO,), -1, now=now) if s in ("overdue", "today") for it in items]
    return [t for kind, t, _st in out if kind == "todo"][:limit]
