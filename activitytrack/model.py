# SPDX-License-Identifier: GPL-3.0-or-later
"""
The activity model 1.0 as pure data and functions (app docs/ACTIVITY-MODEL.md; the app's port is
domain/model and domain/todo/TodoPlan.kt, the web's server web/): the vocabulary, the plan of an
activity, the displayed state (§2.2-2.4) and the rounding of instants (§4.2). Nothing here reads or
writes the store.

An activity is the `entry` record: kind ACTIVITY or ABSENCE, status TODO, RUNNING or CLOSED,
`deletedUtcMs` set when it is in the bin. Its segments are the `segment` records of its uid.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# kind of an activity
ACTIVITY, ABSENCE = "ACTIVITY", "ABSENCE"
# status
TODO, RUNNING, CLOSED = "TODO", "RUNNING", "CLOSED"
# kind of a segment (ABSENCE is the same word)
WORK, TRAVEL, PAUSE = "WORK", "TRAVEL", "PAUSE"
# what an absence is
ABSENCE_KINDS = (ABSENCE, "HOLIDAY", "LEAVE", "SICK")
# priority, 0 none to 5 emergency
PRIORITIES = (0, 1, 2, 3, 4, 5)

# the displayed states (§2.2)
DELETED, LATE, PLANNED, SUSPENDED, DONE, INCOMPLETE = (
    "DELETED", "LATE", "PLANNED", "SUSPENDED", "DONE", "INCOMPLETE")
# also TODO, WORK, TRAVEL, PAUSE and ABSENCE, which are the words above
DISPLAY_STATES = (DELETED, ABSENCE, LATE, PLANNED, TODO, WORK, TRAVEL, PAUSE, SUSPENDED, DONE, INCOMPLETE)

# how far after now a closing time may lie (§4.1)
FUTURE_CLOSE_MS = 60 * 60_000
# the quantum of an instant of a segment of each kind, in minutes (ReportRounding.DEFAULT; §4.2)
DEFAULT_QUANTA = {WORK: 15, TRAVEL: 5, PAUSE: 5}


def tz_of(zone):
    """A tzinfo from an IANA name (UTC when unknown) or a tzinfo."""
    if zone is None or isinstance(zone, str):
        try:
            return ZoneInfo(zone or "UTC")
        except (KeyError, ValueError):
            return ZoneInfo("UTC")
    return zone


def day_of(text):
    """The date of a "yyyy-mm-dd" (None when empty or wrong)."""
    try:
        return date.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def at_minute(day, minute, tz):
    """[minute] after the local midnight of [day] (a wall clock, right on DST days)."""
    minute = min(max(minute, 0), 1440)
    if minute == 1440:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)


def to_ms(moment):
    return int(moment.timestamp() * 1000)


def from_ms(ms, tz):
    return datetime.fromtimestamp(ms / 1000, tz)


# ---- the plan and the due date of an activity ----

def plan_bounds(a, tz):
    """(start, end, all day) of the plan of [a], or None: a whole day, a point in time or a block."""
    day = day_of(a.get("planDate"))
    if day is None:
        return None
    start_min = a.get("planStartMin")
    if start_min is None:
        return at_minute(day, 0, tz), at_minute(day + timedelta(days=1), 0, tz), True
    start = at_minute(day, start_min, tz)
    return start, start + timedelta(minutes=a.get("durationMin") or 0), False


def plan_over(a, now, tz):
    """The plan is over: after its end, a whole-day plan from the next day (TodoPlan.over)."""
    plan = plan_bounds(a, tz)
    return bool(plan) and (now >= plan[1] if plan[2] else now > plan[1])


def due_overdue(a, now, tz):
    """The due date has passed: after the due time, or after the due day without a time (dueOverdue)."""
    due = day_of(a.get("dueDate"))
    if due is None:
        return False
    if a.get("dueTimeMin") is not None:
        limit = at_minute(due, a["dueTimeMin"], tz)
    else:
        limit = at_minute(due + timedelta(days=1), 0, tz)
    return now > limit


def moment(a, tz):
    """The plan's start, else the due moment (todoMoment)."""
    plan = plan_bounds(a, tz)
    if plan:
        return plan[0]
    due = day_of(a.get("dueDate"))
    return at_minute(due, a.get("dueTimeMin") or 0, tz) if due else None


def postponed(a, minutes, now, tz):
    """
    The plan [minutes] after its start, or after now (next whole minute) once the start has passed;
    a whole day moves by days. None without a plan.
    """
    day = day_of(a.get("planDate"))
    if day is None:
        return None
    start_min = a.get("planStartMin")
    if start_min is None:
        return {"planDate": (day + timedelta(days=max(1, (minutes + 1439) // 1440))).isoformat()}
    start = at_minute(day, start_min, tz)
    if now > start:
        local = now.astimezone(tz).replace(tzinfo=None)
        base = local.replace(second=0, microsecond=0)
        if base != local:
            base += timedelta(minutes=1)
    else:
        base = start.replace(tzinfo=None)
    moved = base + timedelta(minutes=minutes)
    return {"planDate": moved.date().isoformat(), "planStartMin": moved.hour * 60 + moved.minute}


# ---- segments ----

def is_planned(s):
    return bool(s.get("planned"))


def real(segments):
    """The segments that count: not planned."""
    return [s for s in segments if not is_planned(s)]


def planned_segments(segments):
    return [s for s in segments if is_planned(s)]


def open_of(segments):
    """The open real segment (the last when the data are wrong), or None."""
    open_ = [s for s in real(segments) if s.get("endUtcMs") is None]
    return open_[-1] if open_ else None


# ---- the displayed state (§2.2-2.4) ----

def is_late(a, planned, now, tz):
    """A TODO activity is late (§2.3); [planned]: its planned segments."""
    if due_overdue(a, now, tz):
        return True
    if planned:
        # a planned segment without an end has not ended
        ends = [s.get("endUtcMs") for s in planned]
        return all(e is not None for e in ends) and to_ms(now) > max(ends)
    return plan_over(a, now, tz)


def is_complete(a, segments, missing_required=False):
    """A CLOSED activity is complete (§2.4)."""
    segs = real(segments)
    if not segs:
        return True
    return bool(a.get("clientUid")) and any(s.get("kind") == WORK for s in segs) and not missing_required


def display(activity, segments, now, zone, missing_required=False):
    """
    (state, since) of an activity as the Home shows it (§2.2); [since] is a datetime in [zone] or
    None. [now] is a datetime or epoch milliseconds, [segments] the activity's segment records,
    [missing_required] a field its company's plugin requires is empty.
    """
    tz = tz_of(zone)
    if isinstance(now, (int, float)):
        now = from_ms(now, tz)
    a = activity

    def when(ms):
        return from_ms(ms, tz) if ms is not None else None

    segs = sorted(segments, key=lambda s: (s.get("startUtcMs") or 0, s.get("endUtcMs") or 2 ** 62))
    done = real(segs)
    plans = planned_segments(segs)

    def last_end():
        ends = [s["endUtcMs"] for s in done if s.get("endUtcMs") is not None]
        return when(max(ends)) if ends else when(a.get("closedUtcMs") if not done else None)

    if a.get("deletedUtcMs") is not None:
        return DELETED, when(a["deletedUtcMs"])
    if a.get("kind") == ABSENCE:
        starts = [s["startUtcMs"] for s in done if s.get("kind") == ABSENCE] or [s["startUtcMs"] for s in done]
        return ABSENCE, when(min(starts)) if starts else None
    status = a.get("status")
    if status == TODO:
        plan = plan_bounds(a, tz)
        since = plan[0] if plan else when(min(s["startUtcMs"] for s in plans)) if plans else None
        if is_late(a, plans, now, tz):
            return LATE, since
        if plan or a.get("dueDate") or plans:
            return PLANNED, since
        return TODO, since
    if status == RUNNING:
        seg = open_of(segs)
        if seg is not None:
            return (seg.get("kind") if seg.get("kind") in (WORK, TRAVEL, PAUSE) else WORK), when(seg["startUtcMs"])
        return SUSPENDED, last_end()
    # CLOSED
    return (DONE if is_complete(a, segs, missing_required) else INCOMPLETE), last_end()


# ---- rounding of the instants of the action buttons (§4.2) ----

def _local_offset_ms(ms, tz):
    off = from_ms(ms, tz).utcoffset()
    return int(off.total_seconds() * 1000) if off else 0


def round_down(ms, quantum_min, tz, not_before=None):
    """The multiple of the quantum (on the local clock) at or before [ms], not before [not_before]."""
    q = quantum_min * 60_000
    off = _local_offset_ms(ms, tz)
    out = (ms + off) // q * q - off
    return max(out, not_before) if not_before is not None else out


def round_up(ms, quantum_min, tz, limit=None):
    """The multiple of the quantum at or after [ms], not after [limit]."""
    q = quantum_min * 60_000
    off = _local_offset_ms(ms, tz)
    out = -((-(ms + off)) // q) * q - off
    return min(out, limit) if limit is not None else out


def round_nearest(ms, quantum_min, tz, not_before=None):
    """The nearest multiple of the quantum (halves up), not before [not_before]."""
    q = quantum_min * 60_000
    off = _local_offset_ms(ms, tz)
    out = (ms + off + q // 2) // q * q - off
    return max(out, not_before) if not_before is not None else out


def priority_of_legacy(value):
    """A priority of the schemas before 3 (1 highest, P1..P5) as 0-5 (§5)."""
    if value is None or value == "":
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    if value < 1:
        return 0
    if value > 5:
        return 1
    return 6 - value


# ---- absences (§3.1) ----

def _merged(windows):
    """The non-empty (start, end) windows in order, those that overlap or touch merged (as the app's AbsencePlanner)."""
    out = []
    for start, end in sorted(w for w in windows if w[0] < w[1]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def working_windows(day, schedule, lunch):
    """
    [(start min, end min)] of the work schedule of [day]'s weekday, split by the lunch break
    (schedule, lunch: [(day of week 1-7, start min, end min)]).
    """
    dow = day.isoweekday()
    breaks = _merged((s, e) for d, s, e in lunch if d == dow)
    out = []
    for start, end in _merged((s, e) for d, s, e in schedule if d == dow):
        cur = start
        for b_start, b_end in breaks:
            if b_end <= cur or b_start >= end:
                continue
            if b_start > cur:
                out.append((cur, b_start))
            cur = max(cur, b_end)
        if cur < end:
            out.append((cur, end))
    return out


def absence_days(first, last):
    """The days from [first] to [last], both included (the last before the first gives the first only)."""
    last = max(first, last)
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def absence_segments(first, last, tz, schedule, lunch, days_off=frozenset(), start_min=None, end_min=None):
    """
    [(start ms, end ms)] of an absence from [first] to [last]: one segment a day with a start and an
    end time, else one per working window of the day; either way the days off and the days without a
    schedule are skipped (owner, 2026-11-11: with times too; a schedule that is empty altogether
    skips nothing but the days off), except a single day with times, taken as chosen (a permesso on
    a Saturday), as the app's AbsencePlanner and the server.
    """
    out = []
    single = first == last
    for day in absence_days(first, last):
        if start_min is not None and end_min is not None:
            if not single and (day in days_off or (schedule and not working_windows(day, schedule, ()))):
                continue
            windows = [(start_min, end_min)]
        elif day in days_off:
            continue
        else:
            windows = working_windows(day, schedule, lunch)
        out += [(to_ms(at_minute(day, s, tz)), to_ms(at_minute(day, e, tz))) for s, e in windows]
    return out
