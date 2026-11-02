# SPDX-License-Identifier: GPL-3.0-or-later
"""
Time selection on a grid of 5-minute cells, as the app (TimeGrid.kt) and
ActivityTrack Web: one row per hour, 12 cells per row, a window of
WINDOW_HOURS rows. Cell i covers minutes [5i, 5i + 5) of the day.
"""

from datetime import datetime, timedelta

STEP = 5
CELLS = 1440 // STEP
WINDOW_HOURS = 12


def cell_of(minute):
    return min(max(minute, 0), 1439) // STEP


def span(a, b):
    """The minutes [start, end) covered by the cells from a to b, in either order."""
    lo, hi = sorted((min(max(a, 0), CELLS - 1), min(max(b, 0), CELLS - 1)))
    return lo * STEP, (hi + 1) * STEP


def window_for(minute):
    """First hour of the window showing the minute with two hours before it, inside the day."""
    return min(max(min(max(minute, 0), 1439) // 60 - 2, 0), 24 - WINDOW_HOURS)


def window_for_work(start, end):
    """The window centred on the working hours [start, end) of the day."""
    if end - start > WINDOW_HOURS * 60:
        return min(max(start // 60, 0), 24 - WINDOW_HOURS)
    return min(max((start + end) // 2 // 60 - WINDOW_HOURS // 2, 0), 24 - WINDOW_HOURS)


def shift(first, hours):
    """The window moved by some hours, inside the day."""
    return min(max(first + hours, 0), 24 - WINDOW_HOURS)


def busy_minutes(segments, day, tz, now_ms, skip=None):
    """
    The minutes of a day covered by the segments (a running one until now),
    clipped to the day and merged: the cells shown hatched. skip: the uid
    of the segment being changed.
    """
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    day_start, day_end = int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)
    parts = []
    for s in segments:
        if s.get("uid") == skip or s.get("deleted") or s.get("startUtcMs") is None:
            continue
        a, b = max(s["startUtcMs"], day_start), min(s.get("endUtcMs") or now_ms, day_end)
        if b > a:
            parts.append(((a - day_start) // 60_000, -(-(b - day_start) // 60_000)))
    out = []
    for a, b in sorted(parts):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
