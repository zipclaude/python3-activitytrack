# SPDX-License-Identifier: GPL-3.0-or-later
"""
The local copy (docs/DESIGN.md §5.1), SQLite:

- `record`: every record as the server last sent it (the shadow);
- `pending`: the local changes not yet pushed, field by field with the
  time of each change; one row per record, in the order of its first change
  (parents before children, as the phone's sync_change);
- `sync_error`: changes the server refused, shown under "Non sincronizzati";
- `state`: cursor, clock offset, last sync.

What the interface reads is `record` overlaid with `pending`.

A store written before the activity model 1.0 (wire schema 3, app docs/ACTIVITY-MODEL.md §6) is migrated
when it is opened (`_migrate_model`, state key "model"): its to-dos, absences and entries become
activities by migration.py, the local changes not yet pushed come along, and the next sync is a full
realignment (the cursor goes back to 0: the server's migrated records win). The realignment, as the
app's (SyncEngine.kt, SyncStore.restamp):

- state "realign": before its first push the sync stamps every pending change with its own clock
  (`restamp`), so they win over the fields the server's migration stamped later; cleared once that push
  went through;
- table `realign_record`: the records the migration wrote into the shadow; each one the server sends is
  struck off, and when the pull from 0 is over (`realigned`) those left, which the server does not have
  (absorbed by another activity there, gone), are dropped unless a local change still holds them.
"""

import logging

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS record (
    type TEXT NOT NULL, uid TEXT NOT NULL, rev INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0, central INTEGER NOT NULL DEFAULT 0,
    fields TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (type, uid)
);
CREATE TABLE IF NOT EXISTS pending (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL, uid TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('upsert', 'delete')),
    fields TEXT NOT NULL DEFAULT '{}', times TEXT NOT NULL DEFAULT '{}', time INTEGER NOT NULL DEFAULT 0,
    UNIQUE (type, uid)
);
CREATE TABLE IF NOT EXISTS sync_error (
    type TEXT NOT NULL, uid TEXT NOT NULL, error TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (type, uid)
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS realign_record (
    type TEXT NOT NULL, uid TEXT NOT NULL,
    PRIMARY KEY (type, uid)
);
"""

# the data model the store holds: 1 is the activity model (wire schema 3); none before
MODEL = 1
# records that hang from an entry, after it in the pending changes
_ENTRY_CHILDREN = ("segment", "externalRef", "attachment", "expense", "entryMaterial")
# an entry's statuses before the activity model, as migration.py maps them
_LEGACY_STATUS = {"ACTIVE": "RUNNING", "SUSPENDED": "RUNNING", "TO_COMPLETE": "CLOSED"}

log = logging.getLogger(__name__)


class Store:
    """One connection per thread on the same file (WAL): the sync writes while the interface reads."""

    def __init__(self, path, zone=None):
        """[zone]: the user's IANA zone, for what the store migration makes of the old to-dos and absences
        (the interface knows it best: Qt's on Windows); None: the system's (see `_zone`)."""
        self.path = str(path)
        self.zone = zone
        self._local = threading.local()
        # every thread's connection, so that close() closes them all (Windows keeps
        # the file of an open one: the data folder could not be removed)
        self._all = []
        self._lock = threading.Lock()
        # close() from this thread ends the store; from another, only that thread's connection
        self._owner = threading.get_ident()
        try:
            # executescript commits by itself: not inside transaction()
            self.db.executescript(SCHEMA)
            self._migrate_model()
        except BaseException:
            self.close()
            raise

    def _migrate_model(self):
        """A store of the schemas 1 and 2 becomes one of the schema 3 (see the module's text)."""
        with self.transaction() as db:
            if self.get_state("model", 0) >= MODEL:
                return
            used = db.execute("SELECT 1 FROM record UNION ALL SELECT 1 FROM pending LIMIT 1").fetchone()
            if used is not None:
                self._migrate_activities(db)
            self.set_state("model", MODEL)

    def _migrate_activities(self, db):
        from . import migration
        from .wire import LEGACY_TYPES
        now = int(time.time() * 1000)
        marks = ",".join("?" * len(migration.READS))
        shadow = {t: {} for t in migration.READS}
        for r in db.execute(f"SELECT type, uid, fields FROM record WHERE deleted = 0 AND type IN ({marks})",
                            migration.READS):
            shadow[r["type"]][r["uid"]] = json.loads(r["fields"])
        pending = db.execute("SELECT * FROM pending ORDER BY seq").fetchall()
        # what the interface saw: the shadow with the changes not yet pushed over it
        merged = {t: {u: dict(f) for u, f in found.items()} for t, found in shadow.items()}
        for p in pending:
            if p["type"] not in merged:
                continue
            if p["op"] == "delete":
                merged[p["type"]].pop(p["uid"], None)
            else:
                merged[p["type"]].setdefault(p["uid"], {}).update(json.loads(p["fields"]))
        zone = self._zone(shadow)
        before = migration.migrate_records(shadow, zone)
        after = migration.migrate_records(merged, zone)
        # the shadow: what the server holds, migrated
        for type_ in LEGACY_TYPES:
            db.execute("DELETE FROM record WHERE type = ?", (type_,))
            db.execute("DELETE FROM sync_error WHERE type = ?", (type_,))
        for type_ in migration.WRITES:
            for uid, fields in before[type_].items():
                old = db.execute("SELECT rev, central FROM record WHERE type = ? AND uid = ?", (type_, uid)).fetchone()
                db.execute(
                    "INSERT OR REPLACE INTO record (type, uid, rev, deleted, central, fields) "
                    "VALUES (?, ?, ?, 0, ?, ?)",
                    (type_, uid, old["rev"] if old else 0, old["central"] if old else 0, json.dumps(fields)),
                )
        # the changes not yet pushed: what turns the migrated shadow into the migrated view
        db.execute("DELETE FROM pending")
        migrated = migration.WRITES + LEGACY_TYPES
        kept = [p for p in pending if p["type"] not in migrated]
        entries = []
        for type_ in migration.WRITES:
            upserts = []
            for uid, fields in after[type_].items():
                base = before[type_].get(uid)
                changed = fields if base is None else {k: v for k, v in fields.items() if base.get(k) != v}
                if changed:
                    upserts.append((type_, uid, "upsert", changed, now))
            # within a type the deletes go first: an external key or a slot they free is taken by an upsert
            entries += [(type_, uid, "delete", {}, now) for uid in before[type_] if uid not in after[type_]]
            entries += upserts
        rows = [p for p in kept if p["type"] not in _ENTRY_CHILDREN]
        rows = [(p["type"], p["uid"], p["op"], json.loads(p["fields"]), json.loads(p["times"]), p["time"])
                for p in rows]
        rows += [(t, u, op, f, {k: at for k in f}, at if op == "delete" else 0) for t, u, op, f, at in entries]
        rows += [(p["type"], p["uid"], p["op"], json.loads(p["fields"]), json.loads(p["times"]), p["time"])
                 for p in kept if p["type"] in _ENTRY_CHILDREN]
        for type_, uid, op, fields, times, at in rows:
            db.execute("INSERT INTO pending (type, uid, op, fields, times, time) VALUES (?, ?, ?, ?, ?, ?)",
                       (type_, uid, op, json.dumps(fields), json.dumps(times), at))
        # the first sync after the migration is a full realignment: the server's version wins, except
        # for the changes not pushed yet, which take the time of that sync (see the module's text)
        db.execute("DELETE FROM realign_record")
        db.executemany("INSERT INTO realign_record (type, uid) VALUES (?, ?)",
                       [(type_, uid) for type_ in migration.WRITES for uid in before[type_]])
        self.set_state("cursor", 0)
        self.set_state("realign", True)

    def _zone(self, shadow):
        """The zone of the store migration: the interface's; else the system's when it is known; else the
        one most of the recorded entries and segments carry (Windows names no IANA zone); else UTC."""
        from collections import Counter
        from .work import system_zone
        if self.zone:
            return self.zone
        found = system_zone()
        if found:
            return found
        zones = Counter(f.get("zoneId") for t in ("entry", "segment") for f in shadow.get(t, {}).values())
        zones.pop(None, None)
        return zones.most_common(1)[0][0] if zones else "UTC"

    @property
    def db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            # each connection stays with its thread; closing it from another one is close()'s only use
            db = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
            try:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA foreign_keys=ON")
            except BaseException:
                db.close()
                raise
            with self._lock:
                self._all.append(db)
            self._local.db = db
        return db

    @contextmanager
    def transaction(self):
        db = self.db
        if db.in_transaction:
            yield db
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    def close(self):
        """
        From the thread that opened the store: every thread's connection, the
        store is not used afterwards. From another thread (a sync worker after
        its run): that thread's connection only, the others go on.
        """
        db = getattr(self._local, "db", None)
        with self._lock:
            if threading.get_ident() == self._owner:
                connections, self._all = self._all, []
            else:
                connections = [db] if db is not None else []
                self._all = [c for c in self._all if c is not db]
        for c in connections:
            c.close()
        self._local.db = None

    # state

    def get_state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_state(self, key, value):
        text = json.dumps(value)
        with self.transaction() as db:
            row = db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
            # the same value is not written again: a write tells the other interfaces the data changed
            if row is None or row[0] != text:
                db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, text))

    @property
    def cursor(self):
        return self.get_state("cursor", 0)

    # what the interface reads

    def get(self, type_, uid):
        """The record's fields with the pending ones over them; None when absent or deleted."""
        rec = self.db.execute("SELECT * FROM record WHERE type = ? AND uid = ?", (type_, uid)).fetchone()
        pend = self.db.execute("SELECT * FROM pending WHERE type = ? AND uid = ?", (type_, uid)).fetchone()
        return _merged(type_, uid, rec, pend)

    def all(self, type_):
        """Every live record of [type_], as get()."""
        recs = {r["uid"]: r for r in self.db.execute("SELECT * FROM record WHERE type = ?", (type_,))}
        pends = {p["uid"]: p for p in self.db.execute("SELECT * FROM pending WHERE type = ? ORDER BY seq", (type_,))}
        out = []
        for uid in list(recs) + [u for u in pends if u not in recs]:
            item = _merged(type_, uid, recs.get(uid), pends.get(uid))
            if item is not None:
                out.append(item)
        return out

    # local changes

    def write(self, type_, uid, fields, now_ms):
        """Changes [fields] of a record (a new one when unknown), each stamped [now_ms]."""
        with self.transaction() as db:
            rec = db.execute("SELECT deleted FROM record WHERE type = ? AND uid = ?", (type_, uid)).fetchone()
            if rec is not None and rec["deleted"]:
                return False
            pend = db.execute("SELECT * FROM pending WHERE type = ? AND uid = ?", (type_, uid)).fetchone()
            if pend is not None and pend["op"] == "delete":
                return False
            old_fields = json.loads(pend["fields"]) if pend else {}
            old_times = json.loads(pend["times"]) if pend else {}
            old_fields.update(fields)
            old_times.update({k: now_ms for k in fields})
            if pend is None:
                db.execute(
                    "INSERT INTO pending (type, uid, op, fields, times) VALUES (?, ?, 'upsert', ?, ?)",
                    (type_, uid, json.dumps(old_fields), json.dumps(old_times)),
                )
            else:
                db.execute(
                    "UPDATE pending SET fields = ?, times = ? WHERE seq = ?",
                    (json.dumps(old_fields), json.dumps(old_times), pend["seq"]),
                )
            return True

    def delete(self, type_, uid, now_ms):
        """Deletes a record; one the server never had just disappears."""
        with self.transaction() as db:
            known = db.execute("SELECT 1 FROM record WHERE type = ? AND uid = ?", (type_, uid)).fetchone()
            if known is None:
                db.execute("DELETE FROM pending WHERE type = ? AND uid = ?", (type_, uid))
                return
            db.execute(
                "INSERT INTO pending (type, uid, op, time) VALUES (?, ?, 'delete', ?) "
                "ON CONFLICT (type, uid) DO UPDATE SET op = 'delete', fields = '{}', times = '{}', "
                "time = excluded.time",
                (type_, uid, now_ms),
            )

    # the sync's side

    def pending(self, limit):
        return self.db.execute("SELECT * FROM pending ORDER BY seq LIMIT ?", (limit,)).fetchall()

    def pending_count(self):
        return self.db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def has_pending(self):
        return self.db.execute("SELECT 1 FROM pending LIMIT 1").fetchone() is not None

    # the realignment after the store migration (see the module's text)

    def realigning(self):
        """A realignment is still to do: a push to restamp, or a pull from 0 to finish."""
        return bool(self.get_state("realign", False)) or self.db.execute(
            "SELECT 1 FROM realign_record LIMIT 1").fetchone() is not None

    def restamp(self, now_ms):
        """Every pending change takes the time [now_ms] (the app's SyncStore.restamp), when one is due."""
        with self.transaction() as db:
            if not self.get_state("realign", False):
                return
            for p in db.execute("SELECT seq, op, fields FROM pending").fetchall():
                times = {} if p["op"] == "delete" else {k: now_ms for k in json.loads(p["fields"])}
                db.execute("UPDATE pending SET times = ?, time = ? WHERE seq = ?",
                           (json.dumps(times), now_ms if p["op"] == "delete" else 0, p["seq"]))

    def restamped(self):
        """The push after the restamp went through: later ones are as usual."""
        self.set_state("realign", False)

    def realigned(self):
        """
        The pull from 0 is over: the migrated records the server did not send are not on the server (its
        migration made them part of another activity, or they went), so they go here too; those a local
        change still holds stay (it was pushed, or will be). Returns how many went.
        """
        with self.transaction() as db:
            left = db.execute(
                "SELECT r.type, r.uid FROM realign_record r WHERE NOT EXISTS "
                "(SELECT 1 FROM pending p WHERE p.type = r.type AND p.uid = r.uid)").fetchall()
            for r in left:
                db.execute("DELETE FROM record WHERE type = ? AND uid = ?", (r["type"], r["uid"]))
                db.execute("DELETE FROM sync_error WHERE type = ? AND uid = ?", (r["type"], r["uid"]))
            db.execute("DELETE FROM realign_record")
        return len(left)

    def legacy_pending(self):
        """
        Changes of the schemas before 3 that an older process sharing the folder wrote after the store
        migration: an entry's are brought to the activity model (statuses, no `todoUid`), to-dos and
        absences cannot be and are dropped. Returns the text of each one dropped, for the user.
        """
        from .wire import LEGACY_TYPES
        dropped = []
        with self.transaction() as db:
            marks = ",".join("?" * len(LEGACY_TYPES))
            for p in db.execute(f"SELECT * FROM pending WHERE type IN ({marks})", LEGACY_TYPES).fetchall():
                db.execute("DELETE FROM pending WHERE seq = ?", (p["seq"],))
                text = f"{p['type']} {p['uid']}: a change of an older version, not synced"
                log.warning("%s", text)
                dropped.append(text)
            for p in db.execute("SELECT * FROM pending WHERE type = 'entry' AND op = 'upsert'").fetchall():
                fields, times = json.loads(p["fields"]), json.loads(p["times"])
                if "todoUid" not in fields and fields.get("status") not in _LEGACY_STATUS:
                    continue
                fields.pop("todoUid", None)
                times.pop("todoUid", None)
                status = fields.get("status")
                if status in _LEGACY_STATUS:
                    fields["status"] = _LEGACY_STATUS[status]
                    if fields["status"] == "CLOSED" and "closedUtcMs" not in fields:
                        fields["closedUtcMs"] = times.get("status") or int(time.time() * 1000)
                        times["closedUtcMs"] = times.get("status") or fields["closedUtcMs"]
                log.warning("entry %s: a change of an older version, brought to the activity model", p["uid"])
                if fields:
                    db.execute("UPDATE pending SET fields = ?, times = ? WHERE seq = ?",
                               (json.dumps(fields), json.dumps(times), p["seq"]))
                else:
                    db.execute("DELETE FROM pending WHERE seq = ?", (p["seq"],))
        return dropped

    @staticmethod
    def change(row):
        """A pending row as a change of sync/push (SYNC.md §5)."""
        if row["op"] == "delete":
            return {"type": row["type"], "uid": row["uid"], "op": "delete", "time": row["time"]}
        return {
            "type": row["type"], "uid": row["uid"], "op": "upsert",
            "fields": json.loads(row["fields"]), "times": json.loads(row["times"]),
        }

    def pushed(self, row, result):
        """
        The server's answer to one pushed change. The fields changed again
        while the push travelled stay pending. Returns the error text, or None.
        """
        type_, uid = row["type"], row["uid"]
        with self.transaction() as db:
            if result.get("status") == "error":
                error = str(result.get("error") or "error")
                detail = str(result.get("detail") or "")
                db.execute(
                    "INSERT OR REPLACE INTO sync_error (type, uid, error, detail) VALUES (?, ?, ?, ?)",
                    (type_, uid, error, detail),
                )
                return f"{type_} {uid}: {error}" + (f" ({detail})" if detail else "")
            db.execute("DELETE FROM sync_error WHERE type = ? AND uid = ?", (type_, uid))
            record = result.get("record")
            if record:
                self._put(db, record)
            now = db.execute("SELECT * FROM pending WHERE seq = ?", (row["seq"],)).fetchone()
            if now is None:
                return None
            if row["op"] == "delete" or (record and record.get("deleted")):
                if now["op"] == "delete" or (record and record.get("deleted")):
                    db.execute("DELETE FROM pending WHERE seq = ?", (row["seq"],))
                return None
            sent = json.loads(row["times"])
            fields, times = json.loads(now["fields"]), json.loads(now["times"])
            for k, t in sent.items():
                if times.get(k) == t:
                    fields.pop(k, None)
                    times.pop(k, None)
            if fields or now["op"] == "delete":
                db.execute(
                    "UPDATE pending SET fields = ?, times = ? WHERE seq = ?",
                    (json.dumps(fields), json.dumps(times), row["seq"]),
                )
            else:
                db.execute("DELETE FROM pending WHERE seq = ?", (row["seq"],))
        return None

    def pulled(self, record):
        with self.transaction() as db:
            self._put(db, record)
            # the server has it: not one to drop at the end of a realignment
            db.execute("DELETE FROM realign_record WHERE type = ? AND uid = ?", (record["type"], record["uid"]))
            if record.get("deleted"):
                # a deleted record cannot be changed again (SYNC.md §6)
                db.execute("DELETE FROM pending WHERE type = ? AND uid = ?", (record["type"], record["uid"]))

    def gone(self, type_, uid):
        """The record left this user's scope: removed without echoing a delete."""
        with self.transaction() as db:
            db.execute("DELETE FROM record WHERE type = ? AND uid = ?", (type_, uid))
            db.execute("DELETE FROM pending WHERE type = ? AND uid = ?", (type_, uid))
            db.execute("DELETE FROM sync_error WHERE type = ? AND uid = ?", (type_, uid))

    def errors(self):
        return self.db.execute("SELECT * FROM sync_error ORDER BY type, uid").fetchall()

    @staticmethod
    def _put(db, r):
        fields = r.get("fields") or {}
        key = fields.get("externalKey")
        if r.get("type") == "entry" and isinstance(key, str) and not key.strip():
            # a blank external key is no key, as on the server
            fields = dict(fields, externalKey=None)
        db.execute(
            "INSERT OR REPLACE INTO record (type, uid, rev, deleted, central, fields) VALUES (?, ?, ?, ?, ?, ?)",
            (
                r["type"], r["uid"], int(r.get("rev") or 0), int(bool(r.get("deleted"))),
                int(bool(r.get("central"))), json.dumps(fields),
            ),
        )

    def reset(self):
        """Forgets everything: a new pairing with another server or user."""
        with self.transaction() as db:
            for table in ("record", "pending", "sync_error", "state", "realign_record"):
                db.execute(f"DELETE FROM {table}")
            # what comes next is of the model the library speaks
            self.set_state("model", MODEL)


def _merged(type_, uid, rec, pend):
    if pend is not None and pend["op"] == "delete":
        return None
    if rec is not None and rec["deleted"]:
        return None
    if rec is None and pend is None:
        return None
    fields = json.loads(rec["fields"]) if rec is not None else {}
    if pend is not None:
        fields.update(json.loads(pend["fields"]))
    fields["uid"] = uid
    fields["_type"] = type_
    fields["_central"] = bool(rec["central"]) if rec is not None else False
    fields["_pending"] = pend is not None
    return fields
