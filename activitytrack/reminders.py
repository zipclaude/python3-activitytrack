# SPDX-License-Identifier: GPL-3.0-or-later
"""
The reminders of the phone, on the desktop (docs/DESIGN.md §7; app
domain/reminder/ReminderPlanner.kt and reminders/ReminderScheduler.kt).
For every source the next reminder; those at or before now are due, the
earliest later one arms the timer.

- confirm: "Ancora su X?" at the last confirmation + interval × (unanswered + 1),
  for an activity at work or travelling, up to maxConfirmations (0 = no limit);
- pause: "Ancora in pausa?" at the last reminder + interval, out of the lunch break;
- day start: "Nessuna attività oggi", the first working interval + delay, on
  working days not off nor handled (an activity started, an absence);
- planned: a planned to-do, its start - lead, once per planned start;
- planned end: the end chosen when starting, while the activity runs.

The last confirmation and the unanswered count stay on this PC, as on the
phone (they do not travel): a new segment starts them again.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .todos import plan_bounds
from .work import ACTIVE, PAUSE, TRAVEL, WORK

CONFIRM, PAUSE_KIND, DAY_START, PLANNED, PLANNED_END = "confirm", "pause", "day_start", "planned", "planned_end"
KINDS = (CONFIRM, PAUSE_KIND, DAY_START, PLANNED, PLANNED_END)

# the app's defaults (AppSettings.kt), the policy's settings over them
DEFAULTS = {"confirmIntervalMin": 30, "maxConfirmations": 0, "pauseIntervalMin": 15, "dayStartDelayMin": 15,
            "planLeadMin": 15, "planSnoozeMin": 30}
LIMITS = {"confirmIntervalMin": (10, 240), "maxConfirmations": (0, 100), "pauseIntervalMin": (10, 120),
          "dayStartDelayMin": (0, 240), "planLeadMin": (0, 240), "planSnoozeMin": (10, 240)}


@dataclass(frozen=True)
class Reminder:
    kind: str
    key: str        # the entry or to-do uid, the day for a day start
    at: datetime


def easter(year):
    """Gregorian Easter Sunday (Meeus/Jones/Butcher), as ItalianHolidays.kt."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * m_) // 451
    return date(year, (h + m_ - 7 * m + 114) // 31, (h + m_ - 7 * m + 114) % 31 + 1)


_FIXED = {(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)}


def italian_holiday(d):
    """National public holidays; 4 October again from 2026; patron saints are manual days off."""
    return ((d.month, d.day) in _FIXED or ((d.month, d.day) == (10, 4) and d.year >= 2026)
            or d == easter(d.year) + timedelta(days=1))


def _at(day, minute, tz):
    from .todos import _at as at
    return at(day, minute, tz)


class Planner:
    """
    schedule, lunch: [(day of week 1–7, start min, end min)]; days_off: a set
    of dates; config: the settings above.
    """

    def __init__(self, config, schedule, lunch, tz, days_off=frozenset()):
        self.config = config
        self.schedule = schedule
        self.lunch = lunch
        self.tz = tz
        self.days_off = days_off

    def off(self, d):
        return d in self.days_off or italian_holiday(d)

    def outside_lunch(self, t):
        local = t.astimezone(self.tz)
        minute = local.hour * 60 + local.minute
        for dow, start, end in self.lunch:
            if dow == local.isoweekday() and start <= minute < end:
                return _at(local.date(), end, self.tz)
        return t

    def planned(self, entries, todos, now, day_start=None):
        """
        entries: [(uid, status, mode, last confirmed, unanswered, planned end)],
        times as datetimes; todos: [(uid, start, end, reminded start)];
        day_start: (last shown day, handled days) or None when switched off.
        """
        c = self.config
        out = []
        for uid, start, end, reminded in todos:
            if reminded != start and now < end:
                out.append(Reminder(PLANNED, uid, start - timedelta(minutes=c["planLeadMin"])))
        for uid, status, mode, confirmed, unanswered, planned_end in entries:
            if status == ACTIVE and planned_end is not None:
                out.append(Reminder(PLANNED_END, uid, planned_end))
            if status != ACTIVE or confirmed is None:
                continue
            if mode in (WORK, TRAVEL):
                if c["maxConfirmations"] == 0 or unanswered < c["maxConfirmations"]:
                    at = confirmed + timedelta(minutes=c["confirmIntervalMin"] * (unanswered + 1))
                    out.append(Reminder(CONFIRM, uid, at))
            elif mode == PAUSE:
                at = self.outside_lunch(confirmed + timedelta(minutes=c["pauseIntervalMin"]))
                out.append(Reminder(PAUSE_KIND, uid, at))
        if day_start is not None:
            r = self._day_start(day_start, now)
            if r is not None:
                out.append(r)
        return out

    def due(self, *args, **kwargs):
        now = args[2] if len(args) > 2 else kwargs["now"]
        return [r for r in self.planned(*args, **kwargs) if r.at <= now]

    def next(self, *args, **kwargs):
        now = args[2] if len(args) > 2 else kwargs["now"]
        later = [r.at for r in self.planned(*args, **kwargs) if r.at > now]
        return min(later) if later else None

    def _day_start(self, state, now):
        last_shown, handled = state
        today = now.astimezone(self.tz).date()
        for offset in range(8):
            day = today + timedelta(days=offset)
            if day == last_shown or day in handled or self.off(day):
                continue
            windows = [w for w in self.schedule if w[0] == day.isoweekday()]
            if not windows:
                continue
            if day == today and now >= _at(day, max(w[2] for w in windows), self.tz):
                continue
            start = _at(day, min(w[1] for w in windows), self.tz)
            return Reminder(DAY_START, day.isoformat(), start + timedelta(minutes=self.config["dayStartDelayMin"]))
        return None


def config_of(policy, local=None):
    """The intervals: the user's own (local), else the policy's, else the app's; the policy's when fixed."""
    out = {}
    for key, default in DEFAULTS.items():
        value = policy.setting(key, default)
        if local and key in local and not policy.setting_fixed(key):
            value = local[key]
        lo, hi = LIMITS[key]
        out[key] = min(max(int(value), lo), hi)
    return out


class Sources:
    """What the planner needs, from the store, plus the local reminder state (state key "reminders")."""

    def __init__(self, work, todos):
        self.work, self.todos = work, todos

    @property
    def store(self):
        return self.work.store

    def state(self):
        return self.store.get_state("reminders", None) or {}

    def save(self, st):
        self.store.set_state("reminders", st)

    def _dt(self, ms, tz):
        return datetime.fromtimestamp(ms / 1000, tz) if ms is not None else None

    def entries(self, tz):
        """The running activities with their confirmation, started again by a new segment."""
        st = self.state()
        confirms = st.get("confirm", {})
        out = []
        changed = False
        for e in self.work.running():
            seg = self.work.open_segment(e["uid"])
            c = confirms.get(e["uid"])
            if seg is not None and (c is None or c.get("segment") != seg["uid"]):
                c = {"segment": seg["uid"], "at": seg["startUtcMs"], "unanswered": 0}
                confirms[e["uid"]] = c
                changed = True
            c = c or {}
            out.append((e["uid"], e.get("status"), e.get("mode"), self._dt(c.get("at"), tz), c.get("unanswered", 0),
                        self._dt(e.get("plannedEndUtcMs"), tz)))
        # the activities no longer running leave no state behind
        running = {e["uid"] for e in self.work.running()}
        for uid in [u for u in confirms if u not in running]:
            del confirms[uid]
            changed = True
        if changed:
            st["confirm"] = confirms
            self.save(st)
        return out

    def planned_todos(self, tz, now):
        """Open to-dos planned at a time (not a whole day), without an activity running of their own."""
        started = {e.get("todoUid") for e in self.work.running() if e.get("status") == ACTIVE}
        reminded = self.state().get("planned", {})
        out = []
        for t in self.todos.all():
            if t.get("done") or t.get("ignoredUtcMs") or t["uid"] in started:
                continue
            plan = plan_bounds(t, tz)
            if plan is None or plan[2]:
                continue
            start, end, _all_day = plan
            # a point in time: its first hour
            end = max(end, start + timedelta(hours=1))
            if now < end:
                r = reminded.get(t["uid"])
                out.append((t["uid"], start, end, self._dt(r, tz) if r else None))
        return out

    def handled_days(self, tz, today):
        """Days with an activity started or an absence; today too while an activity is at work."""
        out = set()
        for a in self.store.all("absence"):
            try:
                out.add(date.fromisoformat(a.get("date")))
            except (TypeError, ValueError):
                pass
        for s in self.store.all("segment"):
            if s.get("startUtcMs"):
                out.add(datetime.fromtimestamp(s["startUtcMs"] / 1000, tz).date())
        if any(e.get("status") == ACTIVE for e in self.work.running()):
            out.add(today)
        return out

    def windows(self, type_):
        return [(r.get("dayOfWeek"), r.get("startMin"), r.get("endMin")) for r in self.store.all(type_)
                if r.get("dayOfWeek") is not None and r.get("startMin") is not None and r.get("endMin") is not None]

    def days_off(self):
        out = set()
        for d in self.store.all("dayOff"):
            try:
                out.add(date.fromisoformat(d.get("date")))
            except (TypeError, ValueError):
                pass
        return out

    # what an answer or a shown reminder changes

    def confirmed(self, uid, at_ms):
        st = self.state()
        c = st.setdefault("confirm", {}).get(uid)
        if c is not None:
            c.update(at=at_ms, unanswered=0)
            self.save(st)

    def reminded(self, r, now_ms):
        st = self.state()
        if r.kind == CONFIRM:
            c = st.setdefault("confirm", {}).get(r.key)
            if c is not None:
                c["unanswered"] = c.get("unanswered", 0) + 1
        elif r.kind == PAUSE_KIND:
            # the next pause reminder counts from now
            c = st.setdefault("confirm", {}).get(r.key)
            if c is not None:
                c["at"] = now_ms
        elif r.kind == PLANNED:
            plan = plan_bounds(self.store.get("todo", r.key) or {}, r.at.tzinfo)
            if plan:
                st.setdefault("planned", {})[r.key] = int(plan[0].timestamp() * 1000)
        elif r.kind == DAY_START:
            st["day_start"] = r.key
        self.save(st)

    def last_confirmed(self, uid):
        return self.state().get("confirm", {}).get(uid, {}).get("at")

    def day_start_state(self, tz, today):
        shown = self.state().get("day_start")
        return (date.fromisoformat(shown) if shown else None), self.handled_days(tz, today)
