# SPDX-License-Identifier: GPL-3.0-or-later
"""
Activities on the desktop, with the app's rules (EntryStateMachine.kt,
EntryRepository.kt; the web's port is server web/ops.py). Every change is
a local write of synced records (store.py), which the sync pushes: the
phone and the web see it as any other device's work.

An entry is ACTIVE with exactly one open segment of its mode, SUSPENDED,
TO_COMPLETE (stopped, details missing) or CLOSED.
"""

import functools
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .policy import Policy
from .sync import now_ms

WORK, TRAVEL, PAUSE = "WORK", "TRAVEL", "PAUSE"
ACTIVE, SUSPENDED, TO_COMPLETE, CLOSED = "ACTIVE", "SUSPENDED", "TO_COMPLETE", "CLOSED"
SUSPEND, STOP, REOPEN = "SUSPEND", "STOP", "REOPEN"
RUNNING = (ACTIVE, SUSPENDED)
# segments recorded as they happen, or typed afterwards
TRACKED, MANUAL = "TRACKED", "MANUAL"


def _make_way(entry):
    """The command that leaves room for another activity: a pause cannot be suspended, it stops."""
    return SUSPEND if entry.get("mode") != PAUSE else STOP


class WorkError(Exception):
    """A command the rules refuse; [code] names why (the interface words it)."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def local_zone():
    """The system's IANA time zone (the interface passes Qt's, which knows Windows too)."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz and "/" in tz:
        return tz
    try:
        target = str(Path("/etc/localtime").resolve())
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def new_uid():
    return str(uuid.uuid4())


# external references, as ExternalRefClassifier.kt
_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$")
_UUID = re.compile(r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$")
_TICKET = re.compile(r"^#?([A-Za-z][A-Za-z0-9_]*[-_ ]?)?[0-9]+$")


def ref_kind(value):
    v = value.strip()
    if _URL.match(v):
        return "URL"
    if _UUID.match(v):
        return "UUID"
    if _TICKET.match(v):
        return "TICKET"
    return "OTHER"


def transition(status, mode, cmd):
    """
    The state machine: (next status, next mode, close the open segment, kind
    to open). Raises WorkError("illegal") for a command the state refuses.
    """
    active = status == ACTIVE
    if cmd in (WORK, TRAVEL, PAUSE):
        if active and mode == cmd:
            raise WorkError("illegal", f"{cmd} in {status}/{mode}")
        if active:
            return ACTIVE, cmd, True, cmd
        if status == SUSPENDED and cmd != PAUSE:
            return ACTIVE, cmd, False, cmd
    elif cmd == SUSPEND:
        if active and mode != PAUSE:
            return SUSPENDED, None, True, None
    elif cmd == STOP:
        if active:
            return TO_COMPLETE, None, True, None
        if status == SUSPENDED:
            return TO_COMPLETE, None, False, None
    elif cmd == REOPEN:
        if status in (TO_COMPLETE, CLOSED):
            return SUSPENDED, None, False, None
    raise WorkError("illegal", f"{cmd} in {status}/{mode}")


def atomic(method):
    """A command writes all its records or none."""
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.store.transaction():
            return method(self, *args, **kwargs)
    return wrapped


@dataclass
class StartRequest:
    """
    A new activity. company, client and role are a record's uid or a name
    typed freely (a new personal record when the policy allows it).
    """
    activity: str = ""
    client: str | None = None
    company: str | None = None
    role: str | None = None
    locality: str = ""
    notes: str = ""
    tags: list = field(default_factory=list)
    mode: str = WORK
    todo: str | None = None
    # a past start, or a finished activity (start and end)
    start_ms: int | None = None
    end_ms: int | None = None
    # what to do with the activities running: {uid: STOP or SUSPEND}; absent = left running
    others: dict = field(default_factory=dict)


class Work:
    def __init__(self, store, policy=None, clock=now_ms, zone=None):
        self.store = store
        self.policy = policy or Policy()
        self.clock = clock
        self.zone = zone or local_zone()

    # ---- reading ----

    def entries(self):
        return self.store.all("entry")

    def entry(self, uid):
        e = self.store.get("entry", uid)
        if e is None:
            raise WorkError("not_found", uid)
        return e

    def segments(self, entry_uid):
        segs = [s for s in self.store.all("segment") if s.get("entryUid") == entry_uid]
        return sorted(segs, key=lambda s: (s.get("startUtcMs") or 0, s.get("endUtcMs") or 2 ** 62))

    def open_segment(self, entry_uid):
        open_ = [s for s in self.segments(entry_uid) if s.get("endUtcMs") is None]
        return open_[-1] if open_ else None

    def running(self):
        """The active activities, then the suspended ones; the latest moved first."""
        out = [e for e in self.entries() if e.get("status") in RUNNING]
        return sorted(out, key=lambda e: (e.get("status") != ACTIVE, -self.last_boundary(e["uid"])))

    def last_boundary(self, entry_uid):
        segs = self.segments(entry_uid)
        return max((max(s.get("startUtcMs") or 0, s.get("endUtcMs") or 0) for s in segs), default=0)

    def totals(self, entry_uid, now=None):
        """Milliseconds of work, travel and pause; an open segment counts until [now]."""
        now = now or self.clock()
        out = {WORK: 0, TRAVEL: 0, PAUSE: 0}
        for s in self.segments(entry_uid):
            end = s.get("endUtcMs") or now
            out[s.get("kind", WORK)] = out.get(s.get("kind", WORK), 0) + max(0, end - s["startUtcMs"])
        return out

    def masters(self, type_, company=None):
        """The live, not archived records of a master type, in their order; clients of [company] only."""
        rows = [r for r in self.store.all(type_) if not r.get("archived")]
        if company is not None and type_ == "client":
            rows = [r for r in rows if r.get("companyUid") == company]
        return sorted(rows, key=lambda r: (r.get("position") is None, r.get("position") or 0,
                                           (r.get("name") or r.get("text") or "").lower()))

    def name(self, type_, uid):
        r = self.store.get(type_, uid) if uid else None
        return (r.get("name") or r.get("text") or "") if r else ""

    def label(self, e):
        """"Client · description", as the app's Home cards."""
        who = self.name("client", e.get("clientUid")) or self.name("company", e.get("companyUid"))
        what = (e.get("activity") or "").strip()
        return " · ".join(x for x in (who, what) if x) or "—"

    # ---- master data typed freely ----

    def resolve(self, type_, value, company=None):
        """
        The uid of [value]: a record's uid, or a name matched without case
        among the live records (clients of the same company first); an
        unknown name becomes a personal record when the policy allows it.
        """
        if not value:
            return None
        value = value.strip()
        if not value:
            return None
        if self.store.get(type_, value) is not None:
            return value
        key = value.casefold()
        found = [r for r in self.store.all(type_) if (r.get("name") or "").strip().casefold() == key]
        if type_ == "client" and company is not None:
            found.sort(key=lambda r: r.get("companyUid") != company)
        live = [r for r in found if not r.get("archived")] or found
        if live:
            return live[0]["uid"]
        if not self.policy.master_free:
            raise WorkError("master_locked", type_)
        uid = new_uid()
        fields = {"name": value, "archived": False}
        if type_ == "client":
            fields["companyUid"] = company
        self.store.write(type_, uid, fields, self.clock())
        return uid

    # ---- commands ----

    def _touch(self, uid, fields, at=None):
        fields = dict(fields, updatedUtcMs=at or self.clock())
        self.store.write("entry", uid, fields, self.clock())

    def _segment(self, entry_uid, kind, start, end=None, source=TRACKED):
        f = {"entryUid": entry_uid, "kind": kind, "startUtcMs": start, "zoneId": self.zone, "source": source}
        if end is not None:
            f["endUtcMs"] = end
        self.store.write("segment", new_uid(), f, self.clock())

    def _min_ms(self):
        return max(0, self.policy.behaviour("minDurationSec", 60)) * 1000

    def check_required(self, values):
        """values: {field: filled}; the first field the policy requires and is empty."""
        for name, filled in values.items():
            if not filled and self.policy.field(name).required:
                raise WorkError("required", name)

    @atomic
    def start(self, req):
        p = self.policy
        if not p.feature("entries"):
            raise WorkError("feature", "entries")
        if req.mode == PAUSE:
            raise WorkError("illegal", "start in PAUSE")
        if req.mode == TRAVEL and not p.feature("travel"):
            raise WorkError("feature", "travel")
        if p.todos_only and not req.todo:
            raise WorkError("todos_only")
        now = self.clock()
        start = req.start_ms or now
        if start > now:
            raise WorkError("future_time")
        if req.end_ms is not None:
            if req.end_ms <= start:
                raise WorkError("invalid_range")
            if req.end_ms - start < self._min_ms():
                raise WorkError("too_short")
        company = self.resolve("company", req.company)
        client = self.resolve("client", req.client, company)
        if client is None and req.mode != TRAVEL:
            raise WorkError("client_required")
        if client is not None:
            client_company = self.store.get("client", client).get("companyUid")
            if company is not None and client_company is not None and client_company != company:
                raise WorkError("client_company_mismatch")
            company = client_company or company
        role = self.resolve("role", req.role)
        self.check_required({"role": role is not None, "locality": bool(req.locality.strip()),
                             "notes": bool(req.notes.strip()), "tags": bool(req.tags)})
        tags = self._tags(req.tags)
        # the activities running: as the user chose, or suspended without concurrent activities
        for e in self.running():
            cmd = req.others.get(e["uid"])
            if cmd is None and not p.feature("concurrentEntries") and e["status"] == ACTIVE:
                cmd = _make_way(e)
            if cmd is not None:
                self.command(e["uid"], cmd, at=min(start, now) if req.start_ms else None)
        uid = new_uid()
        self.store.write("entry", uid, {
            "status": ACTIVE, "mode": req.mode, "zoneId": self.zone,
            "activity": req.activity.strip() or None, "companyUid": company, "clientUid": client, "roleUid": role,
            "todoUid": req.todo, "locality": req.locality.strip() or None, "notes": req.notes.strip() or None,
            "tags": tags, "needsReview": False,
            "plannedEndUtcMs": req.end_ms if req.end_ms is not None and req.end_ms > now else None,
            "createdUtcMs": now, "updatedUtcMs": now,
        }, now)
        self._segment(uid, req.mode, start, source=MANUAL if req.start_ms else TRACKED)
        # the to-do's tickets become the activity's external references
        todo = self.store.get("todo", req.todo) if req.todo else None
        for line in (todo.get("refs") or "").splitlines() if todo else []:
            if line.strip():
                self.store.write("externalRef", new_uid(),
                                 {"entryUid": uid, "kind": ref_kind(line), "value": line.strip()}, now)
        if req.end_ms is not None and req.end_ms <= now:
            self.command(uid, STOP, at=req.end_ms, exact=True)
        return uid

    @atomic
    def command(self, uid, cmd, at=None, exact=False):
        """
        A state-machine command. [at] backdates it, clamped between the
        latest boundary and now; with [exact] a wrong time is an error.
        """
        e = self.entry(uid)
        if cmd == TRAVEL and not self.policy.feature("travel"):
            raise WorkError("feature", "travel")
        status, mode, close, open_kind = transition(e.get("status"), e.get("mode"), cmd)
        now = self.clock()
        latest = self.last_boundary(uid)
        if exact and at is not None:
            if at > now:
                raise WorkError("future_time")
            if at < latest:
                raise WorkError("before_segment")
            when = at
        else:
            when = max(min(at, now) if at is not None else now, latest)
        if close:
            seg = self.open_segment(uid)
            if seg is None:
                raise WorkError("no_open_segment", uid)
            self.store.write("segment", seg["uid"], {"endUtcMs": max(when, seg["startUtcMs"])}, now)
        if open_kind is not None:
            self._segment(uid, open_kind, when)
        # stopped with client and description, not from a to-do: closed at once (owner, 2026-10-19)
        if (status == TO_COMPLETE and self.policy.behaviour("closeCompleteOnStop", True)
                and e.get("clientUid") and (e.get("activity") or "").strip() and not e.get("todoUid")):
            status = CLOSED
        self._touch(uid, {"status": status, "mode": mode}, at=when)
        # without concurrent activities what goes back to work or travel suspends
        # the others, as a new start does (app issue #132)
        if status == ACTIVE and mode != PAUSE and not self.policy.feature("concurrentEntries"):
            for other in self.running():
                if other["uid"] != uid and other["status"] == ACTIVE:
                    self.command(other["uid"], _make_way(other), at=when)
        return status

    @atomic
    def complete(self, uid, allow_empty=False):
        """TO_COMPLETE -> CLOSED: a client, and a description unless the user confirmed without."""
        e = self.entry(uid)
        if e.get("status") != TO_COMPLETE:
            raise WorkError("illegal", f"COMPLETE in {e.get('status')}")
        if not e.get("clientUid"):
            raise WorkError("client_required")
        has_files = any(a.get("entryUid") == uid for a in self.store.all("attachment"))
        if not allow_empty and not (e.get("activity") or "").strip() and not has_files:
            raise WorkError("details_missing")
        self._touch(uid, {"status": CLOSED, "mode": None})

    @atomic
    def close_if_complete(self, uid):
        """After the stop sheet: closed when it has a client and a description."""
        e = self.entry(uid)
        if e.get("status") == TO_COMPLETE and e.get("clientUid") and (e.get("activity") or "").strip():
            self._touch(uid, {"status": CLOSED, "mode": None})
            return True
        return False

    @atomic
    def edit(self, uid, *, activity=None, client=None, company=None, role=None, locality=None, notes=None,
             tags=None):
        """Changes the details given (None = unchanged; "" clears a text)."""
        e = self.entry(uid)
        f = {}
        if activity is not None:
            f["activity"] = activity.strip() or None
        if company is not None:
            f["companyUid"] = self.resolve("company", company)
        if client is not None:
            comp = f.get("companyUid", e.get("companyUid"))
            f["clientUid"] = self.resolve("client", client, comp)
            if f["clientUid"]:
                f["companyUid"] = self.store.get("client", f["clientUid"]).get("companyUid") or comp
        if role is not None:
            f["roleUid"] = self.resolve("role", role)
        if locality is not None:
            f["locality"] = locality.strip() or None
        if notes is not None:
            f["notes"] = notes.strip() or None
        if tags is not None:
            f["tags"] = self._tags(tags)
        if f:
            self._touch(uid, f)

    @atomic
    def delete(self, uid):
        if not self.policy.feature("deleteEntries"):
            raise WorkError("feature", "deleteEntries")
        self.entry(uid)
        self.store.delete("entry", uid, self.clock())

    def _tags(self, names):
        """Tag names; without free tags only those of the master data."""
        known = {(t.get("name") or "").casefold(): t.get("name") for t in self.store.all("tag")}
        out = []
        for n in names:
            n = n.strip()
            if not n:
                continue
            if n.casefold() in known:
                n = known[n.casefold()]
            elif not self.policy.setting("tagsFreeText", True):
                raise WorkError("tag_not_allowed", n)
            if n not in out:
                out.append(n)
        return out

    # ---- times (policy editableTimes) ----

    def _editable(self):
        if not self.policy.setting("editableTimes", True):
            raise WorkError("times_locked")

    def overlapping(self, entry_uid):
        segs = self.segments(entry_uid)
        return any(a.get("endUtcMs") is None or a["endUtcMs"] > b["startUtcMs"] for a, b in zip(segs, segs[1:]))

    def _review(self, entry_uid):
        """The "to check" mark follows the segments (server SYNC.md §11)."""
        e = self.entry(entry_uid)
        over = self.overlapping(entry_uid)
        if bool(e.get("needsReview")) != over:
            self._touch(entry_uid, {"needsReview": over})

    @atomic
    def edit_segment(self, seg_uid, start_ms, end_ms, kind):
        self._editable()
        seg = self.store.get("segment", seg_uid)
        if seg is None:
            raise WorkError("not_found", seg_uid)
        now = self.clock()
        if start_ms > now or (end_ms is not None and end_ms > now):
            raise WorkError("future_time")
        if end_ms is not None and end_ms - start_ms < self._min_ms():
            raise WorkError("too_short" if end_ms > start_ms else "invalid_range")
        if (seg.get("endUtcMs") is None) != (end_ms is None) or (end_ms is None and kind != seg.get("kind")):
            # the running segment stays running, of the entry's mode
            raise WorkError("illegal", "running segment")
        self.store.write("segment", seg_uid, {"startUtcMs": start_ms, "endUtcMs": end_ms, "kind": kind,
                                              "source": MANUAL}, now)
        self._review(seg["entryUid"])

    @atomic
    def add_segment(self, entry_uid, start_ms, end_ms, kind):
        self._editable()
        self.entry(entry_uid)
        if end_ms > self.clock():
            raise WorkError("future_time")
        if end_ms - start_ms < self._min_ms():
            raise WorkError("too_short" if end_ms > start_ms else "invalid_range")
        self._segment(entry_uid, kind, start_ms, end_ms, source=MANUAL)
        self._review(entry_uid)

    @atomic
    def remove_segment(self, seg_uid):
        self._editable()
        seg = self.store.get("segment", seg_uid)
        if seg is None:
            raise WorkError("not_found", seg_uid)
        if seg.get("endUtcMs") is None:
            raise WorkError("illegal", "running segment")
        self.store.delete("segment", seg_uid, self.clock())
        self._review(seg["entryUid"])

    # ---- answers to reminders ----

    @atomic
    def go_on(self, uid):
        """"No, continua" at the planned end: the end is forgotten, on every device."""
        self.entry(uid)
        self._touch(uid, {"plannedEndUtcMs": None})

    @atomic
    def absent_all_day(self, day):
        """An absence for the whole [day] (a date), from the day-start reminder."""
        now = self.clock()
        self.store.write("absence", new_uid(), {"date": day.isoformat(), "kind": "FULL_DAY", "timeMin": None}, now)
