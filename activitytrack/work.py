# SPDX-License-Identifier: GPL-3.0-or-later
"""
Activities on the desktop, with the rules of the activity model 1.0 (app docs/ACTIVITY-MODEL.md §3;
the app's port is EntryStateMachine.kt and EntryRepository.kt, the web's server web/ops.py). Every
change is a local write of synced records (store.py), which the sync pushes: the phone and the web
see it as any other device's work.

An activity is TODO, RUNNING (with at most one open segment, its `mode`), or CLOSED; an absence is
CLOSED with ABSENCE segments; any of them may be deleted (in the bin) and restored. The commands are
start / start_todo, switch, resume, suspend, stop, tick, untick, reopen, delete, restore; pure rules
(displayed state, plan, rounding) are in model.py.
"""

import functools
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import model, timegrid
from .model import (  # noqa: F401  (re-exported: the vocabulary is imported from here)
    ABSENCE, ABSENCE_KINDS, ACTIVITY, CLOSED, DEFAULT_QUANTA, FUTURE_CLOSE_MS, PAUSE, RUNNING, TODO, TRAVEL, WORK,
)
from .policy import Policy
from .sync import now_ms

# commands besides the kind to be in (WORK, TRAVEL, PAUSE)
SUSPEND, STOP, REOPEN, TICK = "SUSPEND", "STOP", "REOPEN", "TICK"
# segments recorded as they happen, or typed afterwards
TRACKED, MANUAL = "TRACKED", "MANUAL"
MAX_DURATION_MIN = 24 * 60

# TODO(activity-1.0-ifaces): the statuses of the schemas before 3, for the interfaces not yet updated.
# Nothing stores them any more: ACTIVE is RUNNING with an open segment, SUSPENDED RUNNING without,
# TO_COMPLETE a CLOSED activity displayed INCOMPLETE.
ACTIVE, SUSPENDED, TO_COMPLETE = "ACTIVE", "SUSPENDED", "TO_COMPLETE"

# the instants of a command (the kinds are WORK, TRAVEL, PAUSE)
_START, _SWITCH = "START", "SWITCH"


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
    The state machine (§3): (next status, next mode, close the open segment, kind to open).
    [mode] is the kind of the open segment, None when there is none. [cmd] is the kind to be in
    (WORK, TRAVEL, PAUSE: start from TODO, switch, resume or reopen, by the state), SUSPEND, STOP or
    TICK. Raises WorkError("illegal") for a command the state refuses.
    """
    if cmd in (WORK, TRAVEL, PAUSE):
        if status == RUNNING and mode is not None:
            if mode == cmd:
                raise WorkError("illegal", f"{cmd} in {status}/{mode}")
            return RUNNING, cmd, True, cmd
        # from TODO (start), RUNNING without an open segment (resume), CLOSED (reopen): never a pause
        if status in (TODO, RUNNING, CLOSED) and cmd != PAUSE:
            return RUNNING, cmd, False, cmd
    elif cmd == SUSPEND:
        if status == RUNNING and mode is not None:
            return RUNNING, None, True, None
    elif cmd == STOP:
        if status == RUNNING:
            return CLOSED, None, mode is not None, None
    elif cmd == TICK:
        if status == TODO:
            return CLOSED, None, False, None
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
    A new activity, or the start of a TODO one ([todo]). company, client and role are a record's uid
    or a name typed freely (a new personal record when the policy allows it).
    """
    activity: str = ""
    client: str | None = None
    company: str | None = None
    role: str | None = None
    locality: str = ""
    notes: str = ""
    tags: list = field(default_factory=list)
    mode: str = WORK
    # the TODO activity to start (its details stay unless given here)
    todo: str | None = None
    # a past start, or a finished activity (start and end)
    start_ms: int | None = None
    end_ms: int | None = None
    # what to do with the activities running: {uid: STOP or SUSPEND}; absent = left running
    others: dict = field(default_factory=dict)


class Work:
    """
    The commands and readings of the activities. [round_stored]: the action buttons store rounded
    instants (§4.2, setting roundStoredTimes, default off); None reads it from the policy. [quanta]:
    the quantum in minutes of each kind of segment, DEFAULT_QUANTA unless given. [notices] collects what
    the interface should tell the user after a command: ("end_moved", uid) when the end in the future of
    another activity moved back to the start of this one (§4.1).
    """

    def __init__(self, store, policy=None, clock=now_ms, zone=None, round_stored=None, quanta=None):
        self.store = store
        self.policy = policy or Policy()
        self.clock = clock
        self.zone = zone or local_zone()
        self.round_stored = round_stored
        self.quanta = dict(DEFAULT_QUANTA, **(quanta or {}))
        self.notices = []

    @property
    def tz(self):
        return model.tz_of(self.zone)

    # ---- reading ----

    def entries(self, deleted=False):
        """The activities: the live ones, [deleted] True the bin's, None all."""
        out = self.store.all("entry")
        if deleted is None:
            return out
        return [e for e in out if (e.get("deletedUtcMs") is not None) == deleted]

    def entry(self, uid):
        e = self.store.get("entry", uid)
        if e is None:
            raise WorkError("not_found", uid)
        return e

    def segments(self, entry_uid):
        """Every segment of an activity, the planned ones too (`planned`), by time."""
        segs = [s for s in self.store.all("segment") if s.get("entryUid") == entry_uid]
        return sorted(segs, key=lambda s: (s.get("startUtcMs") or 0, s.get("endUtcMs") or 2 ** 62))

    def real_segments(self, entry_uid):
        return model.real(self.segments(entry_uid))

    def display(self, e, missing_required=False, now=None):
        """(state, since) of an activity, as model.display."""
        return model.display(e, self.segments(e["uid"]), now if now is not None else self.clock(), self.zone,
                             missing_required)

    def busy(self, day, skip=None):
        """The minutes of a day taken by the activities, for the time grid's hatched cells."""
        live = {e["uid"] for e in self.entries()}
        segs = [s for s in self.store.all("segment") if s.get("entryUid") in live and not model.is_planned(s)]
        return timegrid.busy_minutes(segs, day, self.tz, self.clock(), skip)

    def open_segment(self, entry_uid):
        return model.open_of(self.segments(entry_uid))

    def todo_list(self):
        """The TODO activities (not deleted)."""
        return [e for e in self.entries() if e.get("kind") != ABSENCE and e.get("status") == TODO]

    def running(self):
        """The running activities, those at work first, then the suspended ones; the latest moved first."""
        out = [e for e in self.entries() if e.get("status") == RUNNING and e.get("kind") != ABSENCE]
        opened = {e["uid"]: self.open_segment(e["uid"]) is not None for e in out}
        return sorted(out, key=lambda e: (not opened[e["uid"]], -self.last_boundary(e["uid"])))

    def last_boundary(self, entry_uid):
        segs = self.real_segments(entry_uid)
        return max((max(s.get("startUtcMs") or 0, s.get("endUtcMs") or 0) for s in segs), default=0)

    def totals(self, entry_uid, now=None):
        """Milliseconds of work, travel, pause and absence; an open segment counts until [now]; planned never."""
        now = now or self.clock()
        out = {WORK: 0, TRAVEL: 0, PAUSE: 0, ABSENCE: 0}
        for s in self.real_segments(entry_uid):
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

    # ---- writing ----

    def _touch(self, uid, fields, at=None):
        fields = dict(fields, updatedUtcMs=at or self.clock())
        self.store.write("entry", uid, fields, self.clock())

    def _segment(self, entry_uid, kind, start, end=None, source=TRACKED, planned=False, extra=None):
        f = {"entryUid": entry_uid, "kind": kind, "startUtcMs": start, "zoneId": self.zone, "source": source,
             "planned": planned}
        if end is not None:
            f["endUtcMs"] = end
        f.update(extra or {})
        uid = new_uid()
        self.store.write("segment", uid, f, self.clock())
        return uid

    def _min_ms(self):
        return max(0, self.policy.behaviour("minDurationSec", 60)) * 1000

    def check_required(self, values):
        """values: {field: filled}; the first field the policy requires and is empty."""
        for name, filled in values.items():
            if not filled and self.policy.field(name).required:
                raise WorkError("required", name)

    def _live(self, uid):
        """The activity, which must not be in the bin."""
        e = self.entry(uid)
        if e.get("deletedUtcMs") is not None:
            raise WorkError("illegal", "deleted")
        return e

    def _client_company(self, company, client):
        """(company uid, client uid) once the client's company agrees with the company."""
        if client is not None:
            client_company = self.store.get("client", client).get("companyUid")
            if company is not None and client_company is not None and client_company != company:
                raise WorkError("client_company_mismatch")
            company = client_company or company
        return company, client

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

    # ---- instants: typed, or those of the action buttons, rounded when set (§4.1, §4.2) ----

    def _editable(self):
        if not self.policy.setting("editableTimes", True):
            raise WorkError("times_locked")

    def rounding(self):
        """The action buttons store rounded instants: the setting, effective only with editable times."""
        on = self.round_stored if self.round_stored is not None else self.policy.setting("roundStoredTimes", False)
        return bool(on) and self.policy.setting("editableTimes", True)

    def _floor(self, uid, now):
        """The latest start or end of the activity's segments that is not in the future."""
        best = 0
        for s in self.real_segments(uid):
            for t in (s.get("startUtcMs"), s.get("endUtcMs")):
                if t is not None and t <= now:
                    best = max(best, t)
        return best

    def _instant(self, uid, now, at, exact, op, old_kind=None, new_kind=None):
        """
        The instant of a command: [at] typed (clamped between the latest boundary and now, an error
        when [exact] and it does not fit), else now, rounded for the action buttons when set. A stop
        may lie up to 60 minutes after now when times are editable.
        """
        floor = self._floor(uid, now)
        upper = now + FUTURE_CLOSE_MS if op == STOP and self.policy.setting("editableTimes", True) else now
        if at is not None:
            if exact:
                if at > upper:
                    raise WorkError("future_time")
                if at < floor:
                    raise WorkError("before_segment")
                return at
            return max(min(at, upper), floor)
        when = now
        if self.rounding():
            tz, q = self.tz, self.quanta
            if op == _START:
                when = model.round_down(now, q[new_kind], tz, floor)
            elif op == _SWITCH:
                when = model.round_nearest(now, min(q[old_kind], q[new_kind]), tz, floor)
            elif op == STOP and old_kind is not None:
                when = model.round_up(now, q[old_kind], tz, now + FUTURE_CLOSE_MS)
        return max(when, floor)

    def _new_instant(self, now, kind):
        """The instant of a start with nothing before it: now, rounded down when set."""
        return model.round_down(now, self.quanta[kind], self.tz) if self.rounding() else now

    def _trim_future_ends(self, when, now):
        """
        An activity starts before the end in the future of an earlier one: that end moves back to the
        start, with a notice (§4.1). Absences are in the future by nature and are left alone.
        """
        live = {e["uid"] for e in self.entries()}
        for s in self.store.all("segment"):
            end = s.get("endUtcMs")
            if (s.get("entryUid") in live and end is not None and end > now and end > when
                    and s.get("kind") != ABSENCE and not model.is_planned(s)):
                # not before its own start
                self.store.write("segment", s["uid"], {"endUtcMs": max(when, s["startUtcMs"])}, now)
                self.notices.append(("end_moved", s["entryUid"]))

    # ---- commands ----

    def _way(self, e):
        """The command that leaves room for another activity: a pause cannot be suspended, it stops."""
        seg = self.open_segment(e["uid"])
        return STOP if seg is not None and seg.get("kind") == PAUSE else SUSPEND

    def _make_room(self, others, when):
        """The activities running, as the user chose, or suspended without concurrent activities."""
        for e in self.running():
            cmd = others.get(e["uid"])
            if cmd is None and not self.policy.feature("concurrentEntries") and self.open_segment(e["uid"]):
                cmd = self._way(e)
            if cmd is not None:
                self.command(e["uid"], cmd, at=when)

    def _step(self, uid, cmd, at=None, exact=False, source=TRACKED):
        """One command of the state machine; (the status after it, the instant)."""
        e = self._live(uid)
        if e.get("kind") == ABSENCE:
            raise WorkError("illegal", f"{cmd} in an absence")
        seg = self.open_segment(uid)
        old_kind = seg.get("kind") if seg is not None else None
        status, mode, close, open_kind = transition(e.get("status"), old_kind, cmd)
        if open_kind == TRAVEL and not self.policy.feature("travel"):
            raise WorkError("feature", "travel")
        now = self.clock()
        if cmd == TICK:
            when = now
        else:
            op = cmd if cmd in (STOP, SUSPEND) else _SWITCH if close else _START
            when = self._instant(uid, now, at, exact, op, old_kind, open_kind)
        if e.get("status") == TODO:
            # the plan has served: the real segment replaces the planned ones
            for s in self.segments(uid):
                if model.is_planned(s):
                    self.store.delete("segment", s["uid"], now)
        if close:
            self.store.write("segment", seg["uid"], {"endUtcMs": max(when, seg["startUtcMs"])}, now)
        if open_kind is not None:
            self._trim_future_ends(when, now)
            self._segment(uid, open_kind, when, source=source)
        fields = {"status": status, "mode": mode}
        if status == CLOSED:
            fields["closedUtcMs"] = now
        elif e.get("status") == CLOSED:
            fields["closedUtcMs"] = None
        self._touch(uid, fields, at=min(when, now))
        # without concurrent activities what goes back to work or travel suspends the others,
        # as a new start does (app issue #132)
        if mode in (WORK, TRAVEL) and not self.policy.feature("concurrentEntries"):
            for other in self.running():
                if other["uid"] != uid and self.open_segment(other["uid"]) is not None:
                    self.command(other["uid"], self._way(other), at=when)
        return status, when

    def _check_start(self, kind):
        p = self.policy
        if not p.feature("entries"):
            raise WorkError("feature", "entries")
        if kind == PAUSE:
            raise WorkError("illegal", "start in PAUSE")
        if kind == TRAVEL and not p.feature("travel"):
            raise WorkError("feature", "travel")

    @atomic
    def start(self, req):
        """
        A new activity, RUNNING; with a start and an end in the past, CLOSED ("Passata"). With
        [req].todo the TODO activity starts instead.
        """
        self._check_start(req.mode)
        if req.todo:
            return self._start_from(req)
        if self.policy.planned_only:
            raise WorkError("planned_only")
        now = self.clock()
        start = req.start_ms
        if start is not None and start > now:
            raise WorkError("future_time")
        if req.end_ms is not None:
            if req.end_ms <= (start or now):
                raise WorkError("invalid_range")
            if req.end_ms - (start or now) < self._min_ms():
                raise WorkError("too_short")
        company = self.resolve("company", req.company)
        client = self.resolve("client", req.client, company)
        if client is None and req.mode != TRAVEL:
            raise WorkError("client_required")
        company, client = self._client_company(company, client)
        role = self.resolve("role", req.role)
        self.check_required({"role": role is not None, "locality": bool(req.locality.strip()),
                             "notes": bool(req.notes.strip()), "tags": bool(req.tags)})
        tags = self._tags(req.tags)
        when = start if start is not None else self._new_instant(now, req.mode)
        self._make_room(req.others, when)
        uid = new_uid()
        self.store.write("entry", uid, {
            "kind": ACTIVITY, "status": RUNNING, "mode": req.mode, "zoneId": self.zone,
            "activity": req.activity.strip() or None, "companyUid": company, "clientUid": client, "roleUid": role,
            "locality": req.locality.strip() or None, "notes": req.notes.strip() or None,
            "tags": tags, "needsReview": False, "priority": 0,
            "plannedEndUtcMs": req.end_ms if req.end_ms is not None and req.end_ms > now else None,
            "createdUtcMs": now, "updatedUtcMs": now,
        }, now)
        self._trim_future_ends(when, now)
        self._segment(uid, req.mode, when, source=MANUAL if start is not None else TRACKED)
        if req.end_ms is not None and req.end_ms <= now:
            self._step(uid, STOP, at=req.end_ms, exact=True)
        return uid

    def _start_from(self, req):
        """The TODO activity [req].todo starts; what the request names replaces its details."""
        e = self._live(req.todo)
        if e.get("status") != TODO or e.get("kind") == ABSENCE:
            raise WorkError("illegal", f"start in {e.get('status')}")
        now = self.clock()
        if req.start_ms is not None and req.start_ms > now:
            raise WorkError("future_time")
        f = {}
        company = self.resolve("company", req.company) if req.company else e.get("companyUid")
        client = self.resolve("client", req.client, company) if req.client else e.get("clientUid")
        if client is None and req.mode != TRAVEL:
            raise WorkError("client_required")
        f["companyUid"], f["clientUid"] = self._client_company(company, client)
        if req.role:
            f["roleUid"] = self.resolve("role", req.role)
        for key, value in (("activity", req.activity), ("locality", req.locality), ("notes", req.notes)):
            if value.strip():
                f[key] = value.strip()
        if req.tags:
            f["tags"] = self._tags(req.tags)
        if req.end_ms is not None and req.end_ms > now:
            f["plannedEndUtcMs"] = req.end_ms
        self.check_required({"role": bool(f.get("roleUid", e.get("roleUid"))),
                             "locality": bool(f.get("locality", e.get("locality"))),
                             "notes": bool(f.get("notes", e.get("notes"))),
                             "tags": bool(f.get("tags", e.get("tags")))})
        when = req.start_ms if req.start_ms is not None else self._new_instant(now, req.mode)
        self._make_room(req.others, when)
        self._touch(req.todo, f)
        self._step(req.todo, req.mode, at=req.start_ms, source=MANUAL if req.start_ms is not None else TRACKED)
        if req.end_ms is not None and req.end_ms <= now:
            self._step(req.todo, STOP, at=req.end_ms, exact=True)
        return req.todo

    @atomic
    def start_todo(self, uid, kind=WORK, at=None, exact=False):
        """TODO -> RUNNING: the planned segments go, [kind] (WORK or TRAVEL) opens."""
        self._check_start(kind)
        if self.entry(uid).get("status") != TODO:
            raise WorkError("illegal", "start")
        self._step(uid, kind, at, exact, source=MANUAL if at is not None else TRACKED)
        return uid

    @atomic
    def switch(self, uid, kind, at=None, exact=False):
        """RUNNING with an open segment of another kind: close it and open [kind] at one instant."""
        if self.open_segment(uid) is None:
            raise WorkError("illegal", "switch")
        return self._step(uid, kind, at, exact)[0]

    @atomic
    def resume(self, uid, kind=WORK, at=None, exact=False):
        """RUNNING without an open segment: open [kind] (WORK or TRAVEL)."""
        if self.entry(uid).get("status") != RUNNING or self.open_segment(uid) is not None:
            raise WorkError("illegal", "resume")
        return self._step(uid, kind, at, exact)[0]

    @atomic
    def suspend(self, uid, at=None, exact=False):
        """Internal, no button: close the open segment; what happens to the others when one starts."""
        return self._step(uid, SUSPEND, at, exact)[0]

    @atomic
    def stop(self, uid, at=None, exact=False, return_trip=False):
        """
        RUNNING -> CLOSED: the open segment ends at [at] (up to 60 minutes after now with editable
        times), `closedUtcMs` is now. With [return_trip] the return travel is added (§3.2, see
        return_trip_offered).
        """
        outbound = None
        if return_trip:
            self._editable()
            outbound = self._outbound(uid)
            if outbound is None:
                raise WorkError("illegal", "no return trip")
        status, when = self._step(uid, STOP, at, exact)
        if outbound is not None:
            extra = {k: outbound[k] for k in ("kmEstimated", "kmDeclared") if outbound.get(k) is not None}
            # no location samples on the desktop: the return has none
            self._segment(uid, TRAVEL, when, when + outbound["endUtcMs"] - outbound["startUtcMs"], source=MANUAL,
                          extra=extra)
        return status

    @atomic
    def tick(self, uid):
        """TODO -> CLOSED, a task ticked off: the planned segments go."""
        return self._step(uid, TICK)[0]

    @atomic
    def untick(self, uid):
        """A task ticked off (CLOSED, no real segment) is TODO again (the library's own rule, §3 has none)."""
        e = self._live(uid)
        if e.get("kind") == ABSENCE or e.get("status") != CLOSED or self.real_segments(uid):
            raise WorkError("illegal", "untick")
        self._touch(uid, {"status": TODO, "mode": None, "closedUtcMs": None})

    @atomic
    def reopen(self, uid, kind=WORK, at=None, exact=False):
        """CLOSED -> RUNNING: [kind] opens at [at]; the gap stays a gap. The interface asks first."""
        self._check_start(kind)
        if self.entry(uid).get("status") != CLOSED:
            raise WorkError("illegal", "reopen")
        return self._step(uid, kind, at, exact, source=MANUAL if at is not None else TRACKED)[0]

    @atomic
    def delete(self, uid):
        """Into the bin (restorable): `deletedUtcMs` is now."""
        e = self._live(uid)
        if e.get("kind") != ABSENCE and e.get("status") == TODO:
            # ignoreTodos maps to delete/restore (§7)
            if not (self.policy.feature("deleteTodos") or self.policy.feature("ignoreTodos")):
                raise WorkError("feature", "deleteTodos")
        elif not self.policy.feature("deleteEntries"):
            raise WorkError("feature", "deleteEntries")
        self._touch(uid, {"deletedUtcMs": self.clock()})

    @atomic
    def restore(self, uid):
        if self.entry(uid).get("deletedUtcMs") is None:
            raise WorkError("illegal", "restore")
        self._touch(uid, {"deletedUtcMs": None})

    # ---- the choices of the travel and stop sheets (§3, §3.2) ----

    def asks_completion(self, uid):
        """Travel is asked from work: the interface asks "Hai completato l'attività lavorativa?"."""
        seg = self.open_segment(uid)
        return self.entry(uid).get("status") == RUNNING and seg is not None and seg.get("kind") == WORK

    @atomic
    def travel_from_work(self, uid, completed, at=None):
        """
        The answer to that question: yes closes the activity at the instant and starts a new anonymous
        one travelling from the same instant; no switches to TRAVEL in the same activity. The uid of
        the activity travelling is returned.
        """
        self._check_start(TRAVEL)
        if not self.asks_completion(uid):
            raise WorkError("illegal", "travel from work")
        if not completed:
            self.switch(uid, TRAVEL, at)
            return uid
        now = self.clock()
        when = self._instant(uid, now, at, False, _SWITCH, WORK, TRAVEL)
        self._step(uid, STOP, at=when)
        new = new_uid()
        self.store.write("entry", new, {
            "kind": ACTIVITY, "status": RUNNING, "mode": TRAVEL, "zoneId": self.zone, "needsReview": False,
            "priority": 0, "createdUtcMs": now, "updatedUtcMs": now,
        }, now)
        self._segment(new, TRAVEL, when)
        return new

    def _outbound(self, uid):
        """
        The outbound travel of §3.2: the last TRAVEL segment before the first WORK segment that follows
        a travel, when no TRAVEL comes after the last WORK segment; else None.
        """
        segs = [s for s in self.real_segments(uid) if s.get("kind") in (WORK, TRAVEL)]
        kinds = [s["kind"] for s in segs]
        if TRAVEL not in kinds or WORK not in kinds:
            return None
        work_after = [i for i, k in enumerate(kinds) if k == WORK and i > kinds.index(TRAVEL)]
        if not work_after:
            return None
        last_work = max(i for i, k in enumerate(kinds) if k == WORK)
        if any(k == TRAVEL and i > last_work for i, k in enumerate(kinds)):
            return None
        out = [s for i, s in enumerate(segs) if i < work_after[0] and s["kind"] == TRAVEL]
        return out[-1] if out and out[-1].get("endUtcMs") is not None else None

    def return_trip_offered(self, uid):
        """On stop the interface offers the return trip (§3.2): a TRAVEL segment, later a WORK one, no TRAVEL after."""
        return self.entry(uid).get("status") == RUNNING and self._outbound(uid) is not None

    def return_trip_addable(self, uid):
        """The third choice (stop and add the return trip) needs editable times."""
        return self.return_trip_offered(uid) and bool(self.policy.setting("editableTimes", True))

    def resume_kind(self, uid):
        """"Riprendi" from a pause: the kind of the last WORK or TRAVEL segment starting before it (WORK when none)."""
        pause = self.open_segment(uid)
        limit = pause["startUtcMs"] if pause is not None and pause.get("kind") == PAUSE else None
        for s in reversed(self.real_segments(uid)):
            if s.get("kind") in (WORK, TRAVEL) and (limit is None or s["startUtcMs"] < limit):
                return s["kind"]
        return WORK

    @atomic
    def travel_back(self, uid, at=None):
        """Choice 1 of the stop sheet, "Avvia viaggio di ritorno": TRAVEL now, the activity keeps running."""
        if self.open_segment(uid) is None:
            return self.resume(uid, TRAVEL, at)
        return self.switch(uid, TRAVEL, at)

    # ---- what the interfaces called before 1.0 ----

    @atomic
    def command(self, uid, cmd, at=None, exact=False):
        """
        One command by name, as the interfaces gave it before 1.0: WORK, TRAVEL, PAUSE start, switch,
        resume or reopen by the state; SUSPEND, STOP, REOPEN (opens WORK), TICK. Returns the new status.
        TODO(activity-1.0-ifaces): the interfaces call the named commands instead.
        """
        status = self.entry(uid).get("status")
        if cmd == REOPEN:
            cmd = WORK
        if cmd in (WORK, TRAVEL, PAUSE):
            if status == TODO:
                self._check_start(cmd)
                self.start_todo(uid, cmd, at, exact)
                return RUNNING
            if status == CLOSED:
                return self.reopen(uid, cmd, at, exact)
            return self.switch(uid, cmd, at, exact) if self.open_segment(uid) else self.resume(uid, cmd, at, exact)
        if cmd == STOP:
            return self.stop(uid, at, exact)
        if cmd == SUSPEND:
            return self.suspend(uid, at, exact)
        if cmd == TICK:
            return self.tick(uid)
        raise WorkError("illegal", cmd)

    @atomic
    def complete(self, uid, allow_empty=False):
        """
        What "complete the details" checked after a stop; there is no TO_COMPLETE status any more, a
        closed activity without them is displayed INCOMPLETE. TODO(activity-1.0-ifaces)
        """
        e = self.entry(uid)
        if e.get("status") != CLOSED:
            raise WorkError("illegal", f"COMPLETE in {e.get('status')}")
        if not e.get("clientUid"):
            raise WorkError("client_required")
        has_files = any(a.get("entryUid") == uid for a in self.store.all("attachment"))
        if not allow_empty and not (e.get("activity") or "").strip() and not has_files:
            raise WorkError("details_missing")

    def close_if_complete(self, uid):
        """After the stop sheet: whether it has a client and a description. TODO(activity-1.0-ifaces)"""
        e = self.entry(uid)
        return e.get("status") == CLOSED and bool(e.get("clientUid")) and bool((e.get("activity") or "").strip())

    # ---- details ----

    @atomic
    def edit(self, uid, *, activity=None, client=None, company=None, role=None, locality=None, notes=None,
             tags=None):
        """Changes the details given (None = unchanged; "" clears a text)."""
        e = self._live(uid)
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

    # ---- TODO activities: plan, due date, priority ----

    @atomic
    def save_todo(self, uid, activity, *, company=None, client=None, role=None, notes="", locality="", tags=(),
                  due_date=None, due_min=None, plan_date=None, plan_min=None, duration_min=None, priority=0,
                  planned=None):
        """
        Creates ([uid] None) or changes a TODO activity; returns its uid. [planned]: [(start ms, end ms,
        kind)] planned segments, replacing the ones it has (None leaves them). company, client and role
        are a record's uid or a name typed freely, as for the activities.
        """
        activity = (activity or "").strip()
        if not activity:
            raise WorkError("title_required")
        if plan_date is None and plan_min is not None:
            raise WorkError("plan_day_required")
        if duration_min is not None and plan_min is None:
            raise WorkError("plan_time_required")
        if plan_min is not None and not 0 <= plan_min < 1440:
            raise WorkError("invalid_time")
        if duration_min is not None and not 1 <= duration_min <= MAX_DURATION_MIN:
            raise WorkError("invalid_range")
        priority = priority or 0
        if priority not in model.PRIORITIES:
            raise WorkError("invalid_priority")
        existing = self._live(uid) if uid else None
        if existing is not None and (existing.get("status") != TODO or existing.get("kind") == ABSENCE):
            raise WorkError("illegal", "save_todo")
        if plan_date is not None and not self.policy.feature("planning"):
            if existing is None or existing.get("planDate") != plan_date.isoformat():
                raise WorkError("feature", "planning")
        company_uid = self.resolve("company", company)
        client_uid = self.resolve("client", client, company_uid)
        company_uid, client_uid = self._client_company(company_uid, client_uid)
        self.check_required({"todoPriority": priority > 0, "todoDue": due_date is not None,
                             "todoPlan": plan_date is not None})
        now = self.clock()
        fields = {
            "activity": activity, "companyUid": company_uid, "clientUid": client_uid,
            "roleUid": self.resolve("role", role), "notes": notes.strip() or None,
            "locality": (locality or "").strip() or None, "tags": self._tags(tags),
            "dueDate": due_date.isoformat() if due_date else None, "dueTimeMin": due_min if due_date else None,
            "planDate": plan_date.isoformat() if plan_date else None,
            "planStartMin": plan_min if plan_date else None, "durationMin": duration_min if plan_date else None,
            "priority": priority,
        }
        if uid is None:
            uid = new_uid()
            fields.update(kind=ACTIVITY, status=TODO, mode=None, zoneId=self.zone, needsReview=False,
                          createdUtcMs=now)
        self._touch(uid, fields)
        if planned is not None:
            for s in self.segments(uid):
                if model.is_planned(s):
                    self.store.delete("segment", s["uid"], now)
            for start, end, kind in planned:
                if end <= start:
                    raise WorkError("invalid_range")
                self._segment(uid, kind, start, end, source=MANUAL, planned=True)
        return uid

    @atomic
    def postpone(self, uid, minutes=None, now=None):
        """The plan later by the setting planSnoozeMin (or [minutes]); an activity without plan is left as it is."""
        if not self.policy.feature("planning"):
            raise WorkError("feature", "planning")
        e = self._live(uid)
        minutes = minutes or self.policy.setting("planSnoozeMin", 15)
        moved = model.postponed(e, minutes, now or model.from_ms(self.clock(), self.tz), self.tz)
        if moved is not None:
            self._touch(uid, moved)
        return moved

    # ---- absences (§3.1) ----

    def _windows(self, type_):
        return [(r.get("dayOfWeek"), r.get("startMin"), r.get("endMin")) for r in self.store.all(type_)
                if r.get("dayOfWeek") is not None and r.get("startMin") is not None and r.get("endMin") is not None]

    def _days_off(self):
        days = (model.day_of(d.get("date")) for d in self.store.all("dayOff"))
        return {d for d in days if d is not None}

    @atomic
    def save_absence(self, uid, first, last=None, *, company=None, absence_kind=ABSENCE, notes="", start_min=None,
                     end_min=None):
        """
        Creates ([uid] None) or changes an absence from [first] to [last] (dates; default the same day);
        returns its uid. company None = every company. With start_min and end_min one segment a day;
        without, one per working window of each working day (days without schedule and days off
        skipped). Changing it replaces its segments.
        """
        last = last or first
        if absence_kind not in ABSENCE_KINDS:
            raise WorkError("illegal", absence_kind)
        if (start_min is None) != (end_min is None):
            raise WorkError("invalid_time")
        if start_min is not None and not 0 <= start_min < end_min <= 1440:
            raise WorkError("invalid_range")
        if last < first:
            raise WorkError("invalid_range")
        existing = self._live(uid) if uid else None
        if existing is not None and existing.get("kind") != ABSENCE:
            raise WorkError("illegal", "save_absence")
        spans = model.absence_segments(first, last, self.tz, self._windows("workSchedule"),
                                       self._windows("lunchBreak"), self._days_off(), start_min, end_min)
        if not spans:
            raise WorkError("no_working_days")
        now = self.clock()
        fields = {"companyUid": self.resolve("company", company), "absenceKind": absence_kind,
                  "notes": notes.strip() or None}
        if uid is None:
            uid = new_uid()
            fields.update(kind=ABSENCE, status=CLOSED, mode=None, zoneId=self.zone, needsReview=False, priority=0,
                          closedUtcMs=now, createdUtcMs=now)
        else:
            for s in self.segments(uid):
                self.store.delete("segment", s["uid"], now)
        self._touch(uid, fields)
        for start, end in spans:
            self._segment(uid, ABSENCE, start, end, source=MANUAL)
        return uid

    def add_absence(self, first, last=None, **kw):
        return self.save_absence(None, first, last, **kw)

    def absent_all_day(self, day):
        """An absence for the whole [day] (a date), from the day-start reminder."""
        return self.save_absence(None, day)

    # ---- times (policy editableTimes) ----

    def overlapping(self, entry_uid):
        segs = self.real_segments(entry_uid)
        return any(a.get("endUtcMs") is None or a["endUtcMs"] > b["startUtcMs"] for a, b in zip(segs, segs[1:]))

    def _review(self, entry_uid):
        """The "to check" mark follows the segments (server SYNC.md §11)."""
        e = self.entry(entry_uid)
        over = self.overlapping(entry_uid)
        if bool(e.get("needsReview")) != over:
            self._touch(entry_uid, {"needsReview": over})

    def _is_last(self, seg):
        """The segment is the last real one of its activity: its end may lie up to 60 minutes ahead (§4.1)."""
        real = self.real_segments(seg["entryUid"])
        return bool(real) and real[-1]["uid"] == seg["uid"]

    def _check_times(self, e, seg, start_ms, end_ms, planned):
        """The times of a segment of [e] against the future and the length (planned and absence ones may lie ahead)."""
        now = self.clock()
        if not (planned or e.get("kind") == ABSENCE):
            if start_ms > now:
                raise WorkError("future_time")
            if end_ms is not None and end_ms > now:
                if not ((seg is None or self._is_last(seg)) and end_ms <= now + FUTURE_CLOSE_MS):
                    raise WorkError("future_time")
        if end_ms is not None and end_ms - start_ms < self._min_ms():
            raise WorkError("too_short" if end_ms > start_ms else "invalid_range")

    @staticmethod
    def _kind_fits(e, kind, planned):
        """ABSENCE segments belong to absences, never planned; an absence has no other kind."""
        if (kind == ABSENCE) != (e.get("kind") == ABSENCE) or (planned and kind == ABSENCE):
            raise WorkError("illegal", f"{kind} segment in {e.get('kind')}")

    @atomic
    def edit_segment(self, seg_uid, start_ms, end_ms, kind):
        self._editable()
        seg = self.store.get("segment", seg_uid)
        if seg is None:
            raise WorkError("not_found", seg_uid)
        e = self._live(seg["entryUid"])
        planned = model.is_planned(seg)
        if (seg.get("endUtcMs") is None) != (end_ms is None) or (end_ms is None and kind != seg.get("kind")):
            # the running segment stays running, of the entry's mode
            raise WorkError("illegal", "running segment")
        self._kind_fits(e, kind, planned)
        self._check_times(e, seg, start_ms, end_ms, planned)
        self.store.write("segment", seg_uid, {"startUtcMs": start_ms, "endUtcMs": end_ms, "kind": kind,
                                              "source": MANUAL}, self.clock())
        self._review(seg["entryUid"])

    @atomic
    def add_segment(self, entry_uid, start_ms, end_ms, kind, planned=False):
        """A finished segment; [planned] only in a TODO activity, which may lie ahead."""
        self._editable()
        e = self._live(entry_uid)
        if planned and (e.get("status") != TODO or e.get("kind") == ABSENCE):
            raise WorkError("illegal", "planned segment")
        self._kind_fits(e, kind, planned)
        self._check_times(e, None, start_ms, end_ms, planned)
        self._segment(entry_uid, kind, start_ms, end_ms, source=MANUAL, planned=planned)
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
        self._live(uid)
        self._touch(uid, {"plannedEndUtcMs": None})
