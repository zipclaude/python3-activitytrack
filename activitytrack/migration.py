# SPDX-License-Identifier: GPL-3.0-or-later
"""
The data migration of the activity model 1.0 (app docs/ACTIVITY-MODEL.md §6, 3.1, 5) for the PC's local
store: schemas 1 and 2 (to-dos, absences, entries linked to a to-do) become schema 3 (the activity).

The part up to `normalize` is the app repository's reference implementation, `scripts/activity_migration.py`
(branch feature/activity-1.0, commit 2c96bec, GPL-3.0-or-later like this library), kept as it is so that the
shared cases `docs/samples/activity-migration.json` (tests/data/) pass unchanged: `migrate(rows, zone)` works on the
tables of the app's database 21 (snake_case columns, epoch days, integer ids). What follows it
(`migrate_records`) is this library's: it turns the store's wire records into those rows and back, and
returns what changes.

Conventions (shared with the JSON cases)
- booleans are 0/1 integers, times are epoch milliseconds, days are epoch days, minutes are
  minutes of the local day; a missing key reads as null (`todo.kind` as 'TASK', `todo.done`
  as 0, `todo.refs` as '', `segment.uid` as '').
- `zone`: the zone for everything the migration creates from a to-do or a legacy absence.
  The `todo` table has no zone, so the caller passes one (the app: the device zone; the
  server: the owner's zone). Entries and their segments keep their own `zone_id`.
- Row ids: old rows keep theirs; new rows get ids above the old maximum of their table, in
  the order they are generated (to-dos by id, then legacy absences by id; within a to-do:
  the activity, its segments, its refs). Implementations whose ids come from AUTOINCREMENT
  need not match them: `normalize` (below) drops ids and refers to entries by uid, and that
  is what the cases compare.

Choices where the spec is silent are marked CHOICE.
"""

import json
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NS = uuid.UUID("6f1c2a52-6b1e-5a8e-9d7e-0a5c1e7d1000")

TIME_ENTRY_COLUMNS = [
    "id", "uid", "contact_id", "company_id", "client_id", "role_id", "activity", "status", "mode",
    "locality", "location_kind", "notes", "zone_id", "last_confirmed_utc_ms", "unanswered_reminders",
    "planned_end_utc_ms", "plugin_fields", "created_utc_ms", "updated_utc_ms", "needs_review",
    # new in 22
    "kind", "deleted_utc_ms", "closed_utc_ms", "external_key", "due_epoch_day", "due_time_min",
    "plan_epoch_day", "plan_start_min", "duration_min", "plan_reminded_utc_ms", "priority", "absence_kind",
]
SEGMENT_COLUMNS = [
    "id", "entry_id", "kind", "start_utc_ms", "end_utc_ms", "zone_id", "source", "km_estimated",
    "km_declared", "note", "uid", "planned",
]
EXTERNAL_REF_COLUMNS = ["id", "entry_id", "kind", "value", "uid"]

# CHOICE: the four labels in it/en/de/es (the app's strings.xml only has the "Absence" one).
ABSENCE_LABELS = {
    "ABSENCE": ["Assenza", "Absence", "Abwesenheit", "Ausencia"],
    "HOLIDAY": ["Ferie", "Holiday", "Urlaub", "Vacaciones"],
    "LEAVE": ["Permesso", "Leave", "Freistellung", "Permiso"],
    "SICK": ["Malattia", "Sick leave", "Krankheit", "Enfermedad"],
}
_LABEL_TO_KIND = {lbl.casefold(): k for k, ls in ABSENCE_LABELS.items() for lbl in ls}

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$")
_UUID_RE = re.compile(r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$")
_TICKET_RE = re.compile(r"^#?([A-Za-z][A-Za-z0-9_]*[-_ ]?)?[0-9]+$")


def classify_ref(value):
    """ExternalRefClassifier.classify (domain/model/ExternalRef.kt)."""
    v = value.strip()
    if _URL_RE.match(v):
        return "URL"
    if _UUID_RE.match(v):
        return "UUID"
    if _TICKET_RE.match(v):
        return "TICKET"
    return "OTHER"


def map_priority(p):
    """Section 5: P1..P5 -> 5..1, empty -> 0, > 5 -> 1, < 1 -> 0."""
    if p is None:
        return 0
    if p > 5:
        return 1
    if p < 1:
        return 0
    return 6 - p


def absence_kind(title):
    """Section 6 step 2: the title when it is one of the four labels in any language."""
    return _LABEL_TO_KIND.get((title or "").strip().casefold(), "ABSENCE")


def det_uid(base_uid, suffix):
    return str(uuid.uuid5(NS, f"{base_uid}/{suffix}"))


def _day(epoch_day):
    return date(1970, 1, 1) + timedelta(days=epoch_day)


def _local_ms(day, minute, zone):
    """Wall clock `minute` of `day` in `zone` as epoch ms. A time in a DST gap moves forward
    by the gap, one in an overlap takes the first occurrence (java.time's rule, zoneinfo fold=0)."""
    naive = datetime(day.year, day.month, day.day) + timedelta(minutes=minute)
    aware = naive.replace(tzinfo=zone, fold=0)
    return int(aware.timestamp() * 1000)


def _merge_plugin_fields(todo_json, entry_json):
    """One JSON object: the to-do's values overridden by the entry's. CHOICE: text that is not
    a JSON object counts as empty; the result is null when both are empty, else compact JSON
    with sorted keys."""
    def load(t):
        try:
            v = json.loads(t) if t else None
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}
    merged = {**load(todo_json), **load(entry_json)}
    if not merged:
        return None
    return json.dumps(merged, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _blank(s):
    return s is None or not str(s).strip()


class _Ids:
    def __init__(self, rows):
        self.next = max([r["id"] for r in rows] + [0]) + 1

    def take(self):
        i = self.next
        self.next += 1
        return i


def migrate(rows, zone="Europe/Rome"):
    """rows: {table: [row dict]} of a DB 21; zone: IANA name for what is created from to-dos
    and legacy absences. Returns {"time_entry": [...], "segment": [...], "external_ref": [...]}."""
    tz = ZoneInfo(zone)
    entries = [dict(r) for r in rows.get("time_entry", [])]
    segments = [dict(r) for r in rows.get("segment", [])]
    refs = [dict(r) for r in rows.get("external_ref", [])]
    todos = sorted((dict(r) for r in rows.get("todo", [])), key=lambda r: r["id"])
    legacy = sorted((dict(r) for r in rows.get("absence", [])), key=lambda r: r["id"])

    schedule, lunch = {}, {}
    for r in rows.get("work_schedule", []):
        schedule.setdefault(r["day_of_week"], []).append((r["start_min"], r["end_min"]))
    for r in rows.get("lunch_break", []):
        lunch.setdefault(r["day_of_week"], []).append((r["start_min"], r["end_min"]))
    days_off = {r["date_epoch_day"] for r in rows.get("day_off", [])}

    entry_ids, seg_ids, ref_ids = _Ids(entries), _Ids(segments), _Ids(refs)
    segs_by_entry = {}
    for s in segments:
        segs_by_entry.setdefault(s["entry_id"], []).append(s)

    # ---- step 1: entries -------------------------------------------------------------------
    out_entries = []
    for e in entries:
        own = segs_by_entry.get(e["id"], [])
        status = "RUNNING" if e["status"] in ("ACTIVE", "SUSPENDED") else "CLOSED"  # CHOICE: unknown -> CLOSED
        ends = [s["end_utc_ms"] for s in own if s.get("end_utc_ms") is not None]
        # "last segment end": CHOICE: the latest end of any segment (a closed entry has none open)
        closed = None if status == "RUNNING" else (max(ends) if ends else e["updated_utc_ms"])
        open_segs = sorted((s for s in own if s.get("end_utc_ms") is None), key=lambda s: (s["start_utc_ms"], s["id"]))
        n = {c: e.get(c) for c in TIME_ENTRY_COLUMNS}
        n.update(
            status=status, kind="ACTIVITY", closed_utc_ms=closed, deleted_utc_ms=None, priority=0,
            absence_kind=None,
            # invariant of 1.1: mode = kind of the open segment, null when none. CHOICE: recomputed, not trusted.
            mode=open_segs[-1]["kind"] if open_segs else None,
            unanswered_reminders=e.get("unanswered_reminders") or 0, needs_review=e.get("needs_review") or 0,
        )
        n["_todo_id"] = e.get("todo_id")
        out_entries.append(n)
    by_todo = {}
    for n in out_entries:
        if n["_todo_id"] is not None:
            by_todo.setdefault(n["_todo_id"], []).append(n)

    new_entries, new_segments, new_refs = [], [], []

    def first_start(n):
        starts = [s["start_utc_ms"] for s in segs_by_entry.get(n["id"], [])]
        # CHOICE: an entry without segments sorts after those with
        return (0, min(starts)) if starts else (1, 0)

    def add_ref(entry_n, uid_base, suffix, value, kind=None):
        """One external_ref row unless the entry already has the same value (trimmed)."""
        value = value.strip()
        have = {r["value"].strip() for r in refs if r["entry_id"] == entry_n["id"]}
        have |= {r["value"] for r in new_refs if r["entry_id"] == entry_n["id"]}
        if value in have:
            return
        new_refs.append({
            "id": ref_ids.take(), "entry_id": entry_n["id"], "kind": kind or classify_ref(value), "value": value,
            "uid": det_uid(uid_base, suffix),
        })

    def todo_ref_values(t):
        # CHOICE: one value per non-blank line, trimmed, duplicates dropped keeping the first
        seen, out = set(), []
        for line in (t.get("refs") or "").splitlines():
            v = line.strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def new_activity(uid, created, updated, **kw):
        n = {c: None for c in TIME_ENTRY_COLUMNS}
        n.update(
            id=entry_ids.take(), uid=uid, zone_id=zone, unanswered_reminders=0, needs_review=0,
            created_utc_ms=created, updated_utc_ms=updated,
        )
        n["priority"] = 0
        n.update(kw)
        return n

    def new_segment(entry_n, start, end, seg_uid):
        new_segments.append({
            "id": seg_ids.take(), "entry_id": entry_n["id"], "kind": "ABSENCE", "start_utc_ms": start,
            "end_utc_ms": end, "zone_id": zone, "source": "MANUAL", "km_estimated": None, "km_declared": None,
            "note": None, "uid": seg_uid, "planned": 0,
        })

    def working_intervals(epoch_day):
        """Section 3.1: the weekday's work_schedule minus its lunch_break; none on a day off."""
        if epoch_day in days_off:
            return []
        dow = _day(epoch_day).isoweekday()
        out = []
        # windows that overlap or touch are merged first, as the app's AbsencePlanner does
        merged = []
        for a, b in sorted(schedule.get(dow, [])):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        for a, b in merged:
            cur = a
            for la, lb in sorted(lunch.get(dow, [])):
                if lb <= cur or la >= b:
                    continue
                if la > cur:
                    out.append((cur, la))
                cur = max(cur, lb)
            if cur < b:
                out.append((cur, b))
        return out

    def absence_segments(entry_n, uid_base, first_day, last_day, start_min, duration_min):
        if first_day is None:
            return  # CHOICE: an absence without a day keeps no segment
        if start_min is not None:
            # a "permesso": one segment on the first day. End = start + duration as elapsed time
            # (TodoPlan.end); CHOICE: no duration = zero-length segment (a point in time).
            day = _day(first_day)
            start = _local_ms(day, start_min, tz)
            end = start + (duration_min or 0) * 60000
            new_segment(entry_n, start, end, det_uid(uid_base, f"abs/{day.isoformat()}/0"))
            return
        last_day = first_day if last_day is None or last_day < first_day else last_day
        for ed in range(first_day, last_day + 1):
            day = _day(ed)
            for i, (a, b) in enumerate(working_intervals(ed)):
                # CHOICE: n counts from 0 within the day
                new_segment(entry_n, _local_ms(day, a, tz), _local_ms(day, b, tz),
                            det_uid(uid_base, f"abs/{day.isoformat()}/{i}"))

    # ---- steps 2-3: to-dos -----------------------------------------------------------------
    for t in todos:
        linked = sorted(by_todo.get(t["id"], []), key=lambda n: (first_start(n), n["created_utc_ms"], n["uid"]))
        done = bool(t.get("done"))
        if t.get("kind") == "ABSENCE":
            # step 2. Entries cannot be linked to an absence to-do; CHOICE: if any are, they stay as they are.
            # CHOICE: company/client/notes carried, title kept as description (null when blank), plan, due, priority,
            # key, refs, plugin fields dropped (the days are the segments); CLOSED, closed = last segment end,
            # else updated.
            a = new_activity(
                t["uid"], t["created_utc_ms"], t["updated_utc_ms"], company_id=t.get("company_id"),
                client_id=t.get("client_id"), activity=None if _blank(t.get("title")) else t["title"],
                notes=t.get("notes"), status="CLOSED", kind="ABSENCE", absence_kind=absence_kind(t.get("title")),
            )
            new_entries.append(a)
            absence_segments(a, t["uid"], t.get("plan_epoch_day"), t.get("plan_end_epoch_day"),
                             t.get("plan_start_min"), t.get("duration_min"))
            mine = [s for s in new_segments if s["entry_id"] == a["id"]]
            a["closed_utc_ms"] = max(s["end_utc_ms"] for s in mine) if mine else t["updated_utc_ms"]
            continue
        running = any(n["status"] == "RUNNING" for n in linked)
        todo_fields = dict(
            external_key=t.get("external_key"), due_epoch_day=t.get("due_epoch_day"),
            due_time_min=t.get("due_time_min"), plan_epoch_day=t.get("plan_epoch_day"),
            plan_start_min=t.get("plan_start_min"), duration_min=t.get("duration_min"),
            plan_reminded_utc_ms=t.get("plan_reminded_utc_ms"), priority=map_priority(t.get("priority")),
        )
        if not linked or not (done or running):
            # none linked -> the to-do itself; all closed and the to-do open -> it stays its own TODO
            a = new_activity(
                t["uid"], t["created_utc_ms"], t["updated_utc_ms"], company_id=t.get("company_id"),
                client_id=t.get("client_id"), activity=t["title"], notes=t.get("notes"),
                plugin_fields=_merge_plugin_fields(t.get("plugin_fields"), None), status="CLOSED" if done else "TODO",
                kind="ACTIVITY", closed_utc_ms=t.get("done_utc_ms") if done else None,
                deleted_utc_ms=t.get("ignored_utc_ms"), **todo_fields,
            )
            # CHOICE: a done to-do without done_utc_ms closes at its last update
            if done and a["closed_utc_ms"] is None:
                a["closed_utc_ms"] = t["updated_utc_ms"]
            new_entries.append(a)
            for i, v in enumerate(todo_ref_values(t)):
                add_ref(a, t["uid"], f"ref/{i}", v)
            for n in linked:
                add_ref(n, t["uid"], f"link/{n['uid']}", a["uid"], "UUID")
        else:
            # the earliest entry absorbs the to-do. CHOICE: entry wins for company, client, notes;
            # to-do's ignored flag is dropped here (work must not vanish into the bin).
            head, rest = linked[0], linked[1:]
            head.update(todo_fields)
            head["plugin_fields"] = _merge_plugin_fields(t.get("plugin_fields"), head.get("plugin_fields"))
            if _blank(head.get("activity")):
                head["activity"] = t["title"]
            if _blank(head.get("notes")):
                head["notes"] = t.get("notes")
            for c in ("company_id", "client_id"):
                if head.get(c) is None:
                    head[c] = t.get(c)
            for i, v in enumerate(todo_ref_values(t)):
                add_ref(head, t["uid"], f"ref/{i}", v)
            for n in rest:
                add_ref(n, t["uid"], f"link/{n['uid']}", head["uid"], "UUID")

    # ---- step 4: legacy absence rows -------------------------------------------------------
    for r in legacy:
        kind = r["kind"]
        ed, tmin = r["date_epoch_day"], r.get("time_min")
        sched = sorted(schedule.get(_day(ed).isoweekday(), []))
        first = min((a for a, _ in sched), default=0)  # as the 20 -> 21 migration: no schedule = 0..1440
        last = max((b for _, b in sched), default=1440)
        start = dur = None
        if kind == "FULL_DAY":
            pass
        elif kind == "LATE_ENTRY" and tmin is not None and tmin > first:
            start, dur = first, tmin - first
        elif kind == "EARLY_EXIT" and tmin is not None and tmin < last:
            start, dur = tmin, last - tmin
        else:
            continue  # AbsenceSpan.of is null: nothing was missed (or an unknown kind): no activity
        uid = r.get("uid") or det_uid("absence", str(r["id"]))  # CHOICE: the row's uid; else derived from its id
        # CHOICE: no timestamps on the row: created/updated = the day's start in `zone`
        stamp = _local_ms(_day(ed), 0, tz)
        a = new_activity(uid, stamp, stamp, status="CLOSED", kind="ABSENCE", absence_kind="ABSENCE")
        new_entries.append(a)
        absence_segments(a, uid, ed, ed, start, dur)
        mine = [s for s in new_segments if s["entry_id"] == a["id"]]
        a["closed_utc_ms"] = max(s["end_utc_ms"] for s in mine) if mine else None

    # ---- assemble --------------------------------------------------------------------------
    for n in out_entries:
        n.pop("_todo_id", None)
    old_segments = []
    for s in segments:
        x = {c: s.get(c) for c in SEGMENT_COLUMNS}
        x["uid"] = s.get("uid") or ""
        x["planned"] = 0
        old_segments.append(x)
    old_refs = [{c: r.get(c) for c in EXTERNAL_REF_COLUMNS} for r in refs]
    return {
        "time_entry": [{c: n.get(c) for c in TIME_ENTRY_COLUMNS} for n in out_entries + new_entries],
        "segment": old_segments + new_segments,
        "external_ref": old_refs + new_refs,
    }


def normalize(tables):
    """The comparable form used by the JSON cases: ids dropped, children refer to their entry by
    `entry_uid`, null columns omitted, `plugin_fields` parsed, every table sorted by uid (then by
    start / value). Only `time_entry`, `segment` and `external_ref` are compared."""
    uid_of = {r["id"]: r["uid"] for r in tables["time_entry"]}
    out = {}
    for name in ("time_entry", "segment", "external_ref"):
        rows = []
        for r in tables.get(name, []):
            x = {k: v for k, v in r.items() if v is not None and k != "id"}
            if "entry_id" in x:
                x["entry_uid"] = uid_of[x.pop("entry_id")]
            if "plugin_fields" in x and isinstance(x["plugin_fields"], str):
                x["plugin_fields"] = json.loads(x["plugin_fields"])
            rows.append(x)
        rows.sort(key=lambda x: (x.get("entry_uid", ""), x.get("start_utc_ms", 0), x.get("uid", ""),
                                 x.get("value", "")))
        out[name] = rows
    return out


# ---- this library: the store's wire records as the reference's rows, and back -----------------------

EPOCH = date(1970, 1, 1)

# (wire field, column, conversion): None as it is; "date" yyyy-mm-dd <-> epoch day; "bool" <-> 0/1;
# "json" a JSON value <-> its text
ENTRY_FIELDS = [
    ("contactUid", "contact_id", None), ("companyUid", "company_id", None), ("clientUid", "client_id", None),
    ("roleUid", "role_id", None), ("activity", "activity", None), ("status", "status", None),
    ("mode", "mode", None), ("locality", "locality", None), ("locationKind", "location_kind", None),
    ("notes", "notes", None), ("zoneId", "zone_id", None), ("plannedEndUtcMs", "planned_end_utc_ms", None),
    ("pluginFields", "plugin_fields", "json"), ("needsReview", "needs_review", "bool"),
    ("createdUtcMs", "created_utc_ms", None), ("updatedUtcMs", "updated_utc_ms", None),
    # since schema 3
    ("kind", "kind", None), ("deletedUtcMs", "deleted_utc_ms", None), ("closedUtcMs", "closed_utc_ms", None),
    ("externalKey", "external_key", None), ("dueDate", "due_epoch_day", "date"),
    ("dueTimeMin", "due_time_min", None), ("planDate", "plan_epoch_day", "date"),
    ("planStartMin", "plan_start_min", None), ("durationMin", "duration_min", None),
    ("priority", "priority", None), ("absenceKind", "absence_kind", None),
]
TODO_FIELDS = [
    ("companyUid", "company_id", None), ("clientUid", "client_id", None), ("externalKey", "external_key", None),
    ("title", "title", None), ("notes", "notes", None), ("refs", "refs", None),
    ("dueDate", "due_epoch_day", "date"), ("dueTimeMin", "due_time_min", None), ("priority", "priority", None),
    ("done", "done", "bool"), ("doneUtcMs", "done_utc_ms", None), ("createdUtcMs", "created_utc_ms", None),
    ("updatedUtcMs", "updated_utc_ms", None), ("planDate", "plan_epoch_day", "date"),
    ("planStartMin", "plan_start_min", None), ("durationMin", "duration_min", None),
    ("ignoredUtcMs", "ignored_utc_ms", None), ("pluginFields", "plugin_fields", "json"),
    ("kind", "kind", None), ("planEndDate", "plan_end_epoch_day", "date"),
]
SEGMENT_FIELDS = [
    ("kind", "kind", None), ("startUtcMs", "start_utc_ms", None), ("endUtcMs", "end_utc_ms", None),
    ("zoneId", "zone_id", None), ("source", "source", None), ("kmEstimated", "km_estimated", None),
    ("kmDeclared", "km_declared", None), ("note", "note", None), ("planned", "planned", "bool"),
]
REF_FIELDS = [("kind", "kind", None), ("value", "value", None)]
ABSENCE_FIELDS = [("date", "date_epoch_day", "date"), ("kind", "kind", None), ("timeMin", "time_min", None)]
SCHEDULE_FIELDS = [("dayOfWeek", "day_of_week", None), ("startMin", "start_min", None), ("endMin", "end_min", None)]
DAY_OFF_FIELDS = [("date", "date_epoch_day", "date")]

# the types the migration reads, and the ones it writes
READS = ("entry", "segment", "externalRef", "todo", "absence", "workSchedule", "lunchBreak", "dayOff")
WRITES = ("entry", "segment", "externalRef")


def _to_column(value, conv):
    if value is None:
        return None
    if conv == "date":
        try:
            return (date.fromisoformat(value) - EPOCH).days
        except (TypeError, ValueError):
            return None
    if conv == "bool":
        return 1 if value else 0
    if conv == "json":
        return json.dumps(value, ensure_ascii=False)
    return value


def _to_wire(value, conv):
    if value is None:
        return None
    if conv == "date":
        return (EPOCH + timedelta(days=value)).isoformat()
    if conv == "bool":
        return bool(value)
    if conv == "json":
        try:
            return json.loads(value) if isinstance(value, str) else value
        except ValueError:
            return None
    return value


def _row(spec, fields, **extra):
    row = {col: _to_column(fields.get(name), conv) for name, col, conv in spec if name in fields}
    row.update(extra)
    return row


def _timed(row, **defaults):
    """What the reference reads without asking for it: times and texts a record may lack (never in practice)."""
    for column in ("created_utc_ms", "updated_utc_ms"):
        if row.get(column) is None:
            row[column] = 0
    for column, value in defaults.items():
        if row.get(column) is None:
            row[column] = value
    return row


def _fields(spec, row):
    return {name: _to_wire(row.get(col), conv) for name, col, conv in spec if col in row}


def migrate_records(records, zone="Europe/Rome"):
    """
    records: {type: {uid: fields}} of the store (the wire's field names; the types of READS, the others
    are not looked at). Returns {type: {uid: fields}} for WRITES: every `entry` (the old ones, the to-dos
    that became activities, the absences), `segment` and `externalRef` of the schema 3. `todo` and
    `absence` records are the ones to drop; the other types do not change.
    """
    records = {type_: records.get(type_) or {} for type_ in READS}

    def uids(type_, key=None):
        return sorted(records[type_], key=key or (lambda u: u))

    def by_creation(type_):
        return {u: n for n, u in enumerate(uids(type_, lambda u: (records[type_][u].get("createdUtcMs") or 0, u)), 1)}

    todo_ids, entry_ids = by_creation("todo"), by_creation("entry")
    rows = {
        "todo": [_timed(_row(TODO_FIELDS, records["todo"][u], id=i, uid=u), title="") for u, i in todo_ids.items()],
        "time_entry": [
            _timed(_row(ENTRY_FIELDS, records["entry"][u], id=i, uid=u,
                        todo_id=todo_ids.get(records["entry"][u].get("todoUid"))), status="CLOSED")
            for u, i in entry_ids.items()
        ],
        "segment": [], "external_ref": [], "absence": [],
        "work_schedule": [_row(SCHEDULE_FIELDS, f) for f in records["workSchedule"].values()],
        "lunch_break": [_row(SCHEDULE_FIELDS, f) for f in records["lunchBreak"].values()],
        "day_off": [_row(DAY_OFF_FIELDS, f) for f in records["dayOff"].values()],
    }
    for n, u in enumerate(uids("absence"), 1):
        rows["absence"].append(_row(ABSENCE_FIELDS, records["absence"][u], id=n, uid=u))
    for table, type_, spec in (("segment", "segment", SEGMENT_FIELDS), ("external_ref", "externalRef", REF_FIELDS)):
        for n, u in enumerate(uids(type_), 1):
            f = records[type_][u]
            if f.get("entryUid") in entry_ids:
                rows[table].append(_row(spec, f, id=n, uid=u, entry_id=entry_ids[f["entryUid"]]))
    out = migrate(rows, zone)
    uid_of = {r["id"]: r["uid"] for r in out["time_entry"]}
    result = {"entry": {}, "segment": {}, "externalRef": {}}
    for r in out["time_entry"]:
        fields = _fields(ENTRY_FIELDS, r)
        old = records["entry"].get(r["uid"])
        fields["tags"] = list((old or {}).get("tags") or [])
        result["entry"][r["uid"]] = fields
    for table, type_, spec in (("segment", "segment", SEGMENT_FIELDS), ("external_ref", "externalRef", REF_FIELDS)):
        for r in out[table]:
            result[type_][r["uid"]] = dict(_fields(spec, r), entryUid=uid_of[r["entry_id"]])
    return result
