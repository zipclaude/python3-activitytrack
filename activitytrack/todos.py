# SPDX-License-Identifier: GPL-3.0-or-later
"""
The to-do list of the interfaces, over the activity model 1.0 (app docs/ACTIVITY-MODEL.md: the to-do is
the state TODO of the activity). The plan, the due date and the displayed state are in model.py, the
commands in work.py (save_todo, tick, delete, restore, postpone); what is left here is the Home list
with the app's filters (app ui/home/HomeTabs.kt homeSections; the web's port is server web/homelist.py)
and the names the interfaces used before 1.0.

TODO(activity-1.0-ifaces): the interfaces move to Work and model; this module then goes.
"""

from datetime import datetime, timedelta

from . import model
from .model import at_minute as _at, day_of as _day, moment, plan_bounds, postponed  # noqa: F401
from .work import CLOSED, TODO as STATUS_TODO, WorkError, new_uid, ref_kind

# period of the Home list: -1 today, 0 everything, else hours (setting homeWindow)
WINDOWS = (-1, 24, 72, 168, 720, 0)
TODO, MISSING, DONE, IGNORED = "TODO", "MISSING", "DONE", "IGNORED"
SHOWS = (TODO, MISSING, DONE, IGNORED)
DEFAULT_SHOW = (TODO, MISSING)
SECTIONS = ("overdue", "missing", "today", "next", "earlier", "no_date", "ignored")
MAX_DURATION_MIN = 24 * 60


def overdue(t, now, tz, segments=()):
    """A TODO activity is late (§2.3); a closed or deleted one never."""
    if t.get("status") != STATUS_TODO or t.get("deletedUtcMs") is not None:
        return False
    return model.is_late(t, model.planned_segments(segments), now, tz)


def state(t, now, tz, segments=()):
    """done, ignored (deleted), overdue, planned or open (the names of the to-do's state before 1.0)."""
    if t.get("deletedUtcMs") is not None:
        return "ignored"
    if t.get("status") == CLOSED:
        return "done"
    if overdue(t, now, tz, segments):
        return "overdue"
    return "planned" if t.get("planDate") or t.get("dueDate") or model.planned_segments(segments) else "open"


def sections(todos, stopped, show, window, company=None, tag=None, carry_over_min=360, now=None, tz=None,
             working=frozenset()):
    """
    [(section, [item])], empty sections left out (as the app's Home). todos: TODO activities, the tasks
    ticked off (CLOSED without segments) and the deleted ones; stopped: [(activity, end)] of the closed
    ones with segments (`_display` INCOMPLETE puts one in "missing"); working: uids not shown.
    An item is ("todo", activity, state) or ("entry", activity, end).
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
        st = state(t, now, tz)
        if st in ("done", "ignored"):
            at_ms = (t.get("deletedUtcMs") if st == "ignored" else t.get("closedUtcMs")) or t.get("updatedUtcMs") or 0
            at = datetime.fromtimestamp(at_ms / 1000, tz)
            if st == "done" and DONE in show and (behind is None or at >= behind):
                key = ("1", -at_ms) if at >= start_today else (-at_ms,)
                groups["today" if at >= start_today else "earlier"].append((key, ("todo", t, st)))
            elif st == "ignored" and IGNORED in show and (behind is None or at >= behind):
                groups["ignored"].append(((-at_ms,), ("todo", t, st)))
            continue
        if TODO not in show or t["uid"] in working:
            continue
        m = moment(t, tz)
        if st == "overdue":
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


class Todos:
    """The to-do commands of the interfaces, over the activities' [work]."""

    def __init__(self, work):
        self.work = work

    @property
    def store(self):
        return self.work.store

    @property
    def policy(self):
        return self.work.policy

    def tz(self):
        return model.tz_of(self.work.zone)

    def todo(self, uid):
        e = self.work.entry(uid)
        if e.get("kind") == model.ABSENCE:
            raise WorkError("not_found", uid)
        return e

    def all(self):
        """The TODO activities, the tasks ticked off and the deleted ones (the items of sections)."""
        out = []
        for e in self.work.entries(None):
            if e.get("kind") == model.ABSENCE:
                continue
            if e.get("status") == STATUS_TODO or (e.get("status") == CLOSED and not self.work.real_segments(e["uid"])):
                out.append(e)
        return out

    def home(self, show=DEFAULT_SHOW, window=-1, company=None, tag=None, now=None):
        """The Home list (below the activities running)."""
        work = self.work
        tz = self.tz()
        todos = self.all() if self.policy.feature("todos") else []
        stopped = []
        for e in work.entries():
            if e.get("status") == CLOSED and e.get("kind") != model.ABSENCE and work.real_segments(e["uid"]):
                end = work.last_boundary(e["uid"])
                shown = dict(e, _display=work.display(e)[0])
                stopped.append((shown, datetime.fromtimestamp((end or e.get("updatedUtcMs") or 0) / 1000, tz)))
        carry = self.policy.setting("carryOverMin", 360)
        return sections(todos, stopped, set(show), window, company, tag, carry, now or datetime.now(tz), tz)

    def startable(self, limit=10, now=None):
        """Overdue and today's open to-dos, for the tray menu."""
        tz = self.tz()
        now = now or datetime.now(tz)
        out = [it for s, items in self.home((TODO,), -1, now=now) if s in ("overdue", "today") for it in items]
        return [t for kind, t, _st in out if kind == "todo"][:limit]

    def set_done(self, uid, done=True):
        if done:
            self.work.tick(uid)
        else:
            self.work.untick(uid)

    def set_ignored(self, uid, ignored=True):
        if ignored:
            if not self.policy.feature("ignoreTodos"):
                raise WorkError("feature", "ignoreTodos")
            self.work.delete(uid)
        else:
            self.work.restore(uid)

    def reopen(self, uid):
        """Open again: neither done nor ignored."""
        t = self.todo(uid)
        if t.get("deletedUtcMs") is not None:
            self.work.restore(uid)
        if t.get("status") == CLOSED:
            self.work.untick(uid)

    def postpone(self, uid, minutes=None, now=None):
        return self.work.postpone(uid, minutes, now)

    def save(self, uid, title, *, company=None, client=None, notes="", refs="", due_date=None, due_min=None,
             plan_date=None, plan_min=None, duration_min=None, priority=None):
        """
        Creates ([uid] None) or changes a TODO activity. [priority] is on the new scale (0 none ... 5
        emergency), [refs] one external reference a line (the activity's `externalRef` records).
        """
        uid = self.work.save_todo(uid, title, company=company, client=client, notes=notes, due_date=due_date,
                                  due_min=due_min, plan_date=plan_date, plan_min=plan_min,
                                  duration_min=duration_min, priority=priority or 0)
        now = self.work.clock()
        for r in self.store.all("externalRef"):
            if r.get("entryUid") == uid:
                self.store.delete("externalRef", r["uid"], now)
        for line in (refs or "").splitlines():
            if line.strip():
                self.store.write("externalRef", new_uid(),
                                 {"entryUid": uid, "kind": ref_kind(line), "value": line.strip()}, now)
        return uid

    def delete(self, uid):
        self.work.delete(uid)
