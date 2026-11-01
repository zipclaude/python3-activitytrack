# SPDX-License-Identifier: GPL-3.0-or-later
"""
To-dos on the desktop: their plan and state (app domain/todo/TodoPlan.kt,
data/repo/TodoRepository.kt), the Home list with the app's filters (app
ui/home/HomeTabs.kt homeSections; the web's port is server
web/homelist.py) and the commands: done, ignored, open again, postponed,
new, changed, deleted.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .work import CLOSED, RUNNING, TO_COMPLETE, WorkError, atomic, new_uid

# period of the Home list: -1 today, 0 everything, else hours (setting homeWindow)
WINDOWS = (-1, 24, 72, 168, 720, 0)
TODO, MISSING, DONE, IGNORED = "TODO", "MISSING", "DONE", "IGNORED"
SHOWS = (TODO, MISSING, DONE, IGNORED)
DEFAULT_SHOW = (TODO, MISSING)
SECTIONS = ("overdue", "missing", "today", "next", "earlier", "no_date", "ignored")
MAX_DURATION_MIN = 24 * 60


def _day(text):
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _at(day, minute, tz):
    """[minute] after the local midnight of [day] (a wall clock, right on DST days)."""
    minute = min(max(minute, 0), 1440)
    if minute == 1440:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)


def plan_bounds(t, tz):
    """(start, end, all day) of a to-do's plan, or None (TodoPlan: a whole day, a point or a block)."""
    day = _day(t.get("planDate"))
    if day is None:
        return None
    start_min = t.get("planStartMin")
    if start_min is None:
        start = _at(day, 0, tz)
        return start, _at(day + timedelta(days=1), 0, tz), True
    start = _at(day, start_min, tz)
    duration = t.get("durationMin")
    return start, start + timedelta(minutes=duration or 0), False


def moment(t, tz):
    """The plan's start, else the due moment (todoMoment)."""
    plan = plan_bounds(t, tz)
    if plan:
        return plan[0]
    due = _day(t.get("dueDate"))
    return _at(due, t.get("dueTimeMin") or 0, tz) if due else None


def overdue(t, now, tz):
    if t.get("done") or t.get("ignoredUtcMs"):
        return False
    plan = plan_bounds(t, tz)
    if plan and (now >= plan[1] if plan[2] else now > plan[1]):
        return True
    due = _day(t.get("dueDate"))
    if due:
        if t.get("dueTimeMin") is not None:
            limit = _at(due, t["dueTimeMin"], tz)
        else:
            limit = _at(due + timedelta(days=1), 0, tz)
        return now > limit
    return False


def state(t, now, tz):
    """done, ignored, overdue, planned or open (todoState)."""
    if t.get("done"):
        return "done"
    if t.get("ignoredUtcMs"):
        return "ignored"
    if overdue(t, now, tz):
        return "overdue"
    return "planned" if t.get("planDate") else "open"


def postponed(t, minutes, now, tz):
    """
    The plan [minutes] after its start, or after now (next whole minute)
    once the start has passed; a whole day moves by days. None without a plan.
    """
    day = _day(t.get("planDate"))
    if day is None:
        return None
    start_min = t.get("planStartMin")
    if start_min is None:
        return {"planDate": (day + timedelta(days=max(1, (minutes + 1439) // 1440))).isoformat()}
    start = _at(day, start_min, tz)
    if now > start:
        local = now.astimezone(tz).replace(tzinfo=None)
        base = local.replace(second=0, microsecond=0)
        if base != local:
            base += timedelta(minutes=1)
    else:
        base = start.replace(tzinfo=None)
    moved = base + timedelta(minutes=minutes)
    return {"planDate": moved.date().isoformat(), "planStartMin": moved.hour * 60 + moved.minute}


def sections(todos, stopped, show, window, company=None, tag=None, carry_over_min=360, now=None, tz=None,
             working=frozenset()):
    """
    [(section, [item])], empty sections left out (as the app's Home). todos:
    the user's to-dos; stopped: [(entry, end)] of the closed and details-missing
    entries; working: uids of to-dos with an activity running, shown by it.
    An item is ("todo", to-do, state) or ("entry", entry, end).
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
            at_ms = (t.get("doneUtcMs") or t.get("updatedUtcMs") or 0) if st == "done" else t["ignoredUtcMs"]
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
            groups["no_date"].append(((t.get("priority") or 99, (t.get("title") or "").lower()), ("todo", t, st)))
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
        if e.get("status") == TO_COMPLETE:
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
    """The to-do commands, on the same store and policy as the activities' [work]."""

    def __init__(self, work):
        self.work = work

    @property
    def store(self):
        return self.work.store

    @property
    def policy(self):
        return self.work.policy

    def tz(self):
        try:
            return ZoneInfo(self.work.zone)
        except (KeyError, ValueError):
            return ZoneInfo("UTC")

    def todo(self, uid):
        t = self.store.get("todo", uid)
        if t is None:
            raise WorkError("not_found", uid)
        return t

    def all(self):
        return self.store.all("todo")

    def home(self, show=DEFAULT_SHOW, window=-1, company=None, tag=None, now=None):
        """The Home list (below the activities running)."""
        work = self.work
        tz = self.tz()
        todos = self.all() if self.policy.feature("todos") else []
        stopped = []
        for e in work.entries():
            if e.get("status") in (TO_COMPLETE, CLOSED):
                end = work.last_boundary(e["uid"])
                stopped.append((e, datetime.fromtimestamp((end or e.get("updatedUtcMs") or 0) / 1000, tz)))
        working = {e.get("todoUid") for e in work.entries() if e.get("status") in RUNNING and e.get("todoUid")}
        carry = self.policy.setting("carryOverMin", 360)
        return sections(todos, stopped, set(show), window, company, tag, carry, now or datetime.now(tz), tz, working)

    def startable(self, limit=10, now=None):
        """Overdue and today's open to-dos, for the tray menu."""
        tz = self.tz()
        now = now or datetime.now(tz)
        out = [it for s, items in self.home((TODO,), -1, now=now) if s in ("overdue", "today") for it in items]
        return [t for kind, t, _st in out if kind == "todo"][:limit]

    def _write(self, uid, fields):
        now = self.work.clock()
        self.store.write("todo", uid, dict(fields, updatedUtcMs=now), now)

    @atomic
    def set_done(self, uid, done=True):
        t = self.todo(uid)
        now = self.work.clock()
        # an already done one keeps its time
        self._write(uid, {"done": done, "doneUtcMs": (t.get("doneUtcMs") or now) if done else None,
                          "ignoredUtcMs": None if done else t.get("ignoredUtcMs")})

    @atomic
    def set_ignored(self, uid, ignored=True):
        if ignored and not self.policy.feature("ignoreTodos"):
            raise WorkError("feature", "ignoreTodos")
        t = self.todo(uid)
        if ignored:
            self._write(uid, {"ignoredUtcMs": t.get("ignoredUtcMs") or self.work.clock(), "done": False,
                              "doneUtcMs": None})
        else:
            self._write(uid, {"ignoredUtcMs": None})

    @atomic
    def reopen(self, uid):
        """Open again: neither done nor ignored."""
        self.todo(uid)
        self._write(uid, {"done": False, "doneUtcMs": None, "ignoredUtcMs": None})

    @atomic
    def postpone(self, uid, minutes=None, now=None):
        """The plan later by the setting planSnoozeMin (or [minutes]); a to-do without plan is left as it is."""
        if not self.policy.feature("planning"):
            raise WorkError("feature", "planning")
        tz = self.tz()
        minutes = minutes or self.policy.setting("planSnoozeMin", 15)
        moved = postponed(self.todo(uid), minutes, now or datetime.now(tz), tz)
        if moved is not None:
            self._write(uid, moved)
        return moved

    @atomic
    def save(self, uid, title, *, company=None, client=None, notes="", refs="", due_date=None, due_min=None,
             plan_date=None, plan_min=None, duration_min=None, priority=None):
        """
        Creates ([uid] None) or changes a to-do. company and client are a
        record's uid or a name typed freely, as for the activities.
        """
        title = (title or "").strip()
        if not title:
            raise WorkError("title_required")
        if plan_date is None and plan_min is not None:
            raise WorkError("plan_day_required")
        if duration_min is not None and plan_min is None:
            raise WorkError("plan_time_required")
        if plan_min is not None and not 0 <= plan_min < 1440:
            raise WorkError("invalid_time")
        if duration_min is not None and not 1 <= duration_min <= MAX_DURATION_MIN:
            raise WorkError("invalid_range")
        if priority is not None and not 1 <= priority <= 5:
            raise WorkError("invalid_priority")
        if plan_date is not None and not self.policy.feature("planning"):
            existing = self.store.get("todo", uid) if uid else None
            if existing is None or existing.get("planDate") != plan_date.isoformat():
                raise WorkError("feature", "planning")
        work = self.work
        company_uid = work.resolve("company", company)
        client_uid = work.resolve("client", client, company_uid)
        if client_uid is not None:
            client_company = self.store.get("client", client_uid).get("companyUid")
            if company_uid is not None and client_company is not None and client_company != company_uid:
                raise WorkError("client_company_mismatch")
            company_uid = client_company or company_uid
        work.check_required({"todoPriority": priority is not None, "todoDue": due_date is not None,
                             "todoPlan": plan_date is not None})
        fields = {
            "title": title, "companyUid": company_uid, "clientUid": client_uid,
            "notes": notes.strip() or None,
            "refs": "\n".join(line.strip() for line in (refs or "").splitlines() if line.strip()),
            "dueDate": due_date.isoformat() if due_date else None, "dueTimeMin": due_min if due_date else None,
            "planDate": plan_date.isoformat() if plan_date else None,
            "planStartMin": plan_min if plan_date else None, "durationMin": duration_min if plan_date else None,
            "priority": priority,
        }
        if uid is None:
            uid = new_uid()
            fields.update(done=False, createdUtcMs=work.clock())
        else:
            self.todo(uid)
        self._write(uid, fields)
        return uid

    @atomic
    def delete(self, uid):
        """Only a to-do made by hand: one from a management system is ignored, or it comes back."""
        if not self.policy.feature("deleteTodos"):
            raise WorkError("feature", "deleteTodos")
        if self.todo(uid).get("externalKey"):
            raise WorkError("imported")
        self.store.delete("todo", uid, self.work.clock())
