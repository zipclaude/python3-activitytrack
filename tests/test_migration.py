# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from activitytrack import migration as m
from activitytrack.store import MODEL, Store

DATA = Path(__file__).with_name("data")
DOC = json.loads((DATA / "activity-migration.json").read_text(encoding="utf-8"))

# the app's tables as the store's types, and what each has in the wire
TYPES = {"time_entry": ("entry", m.ENTRY_FIELDS), "segment": ("segment", m.SEGMENT_FIELDS),
         "external_ref": ("externalRef", m.REF_FIELDS), "todo": ("todo", m.TODO_FIELDS),
         "absence": ("absence", m.ABSENCE_FIELDS), "work_schedule": ("workSchedule", m.SCHEDULE_FIELDS),
         "lunch_break": ("lunchBreak", m.SCHEDULE_FIELDS), "day_off": ("dayOff", m.DAY_OFF_FIELDS)}


def records_of(tables):
    """The cases' tables as the store's records: {type: {uid: wire fields}}."""
    uid_of = {t: {r["id"]: r.get("uid") or f"{t}-{r['id']}" for r in tables.get(t, []) if "id" in r}
              for t in ("time_entry", "todo")}
    out = {}
    for table, rows in tables.items():
        type_, spec = TYPES[table]
        found = out.setdefault(type_, {})
        for n, r in enumerate(rows):
            uid = r.get("uid") or f"{table}-{r.get('id', n)}"
            fields = m._fields(spec, r)
            if table == "time_entry" and r.get("todo_id") is not None:
                fields["todoUid"] = uid_of["todo"].get(r["todo_id"], f"missing-{r['todo_id']}")
            if "entry_id" in r:
                fields["entryUid"] = uid_of["time_entry"][r["entry_id"]]
            found[uid] = fields
    return out


def expected_of(after):
    """A case's `after` as records: {type: {uid: fields}}, only what the wire carries."""
    out = {"entry": {}, "segment": {}, "externalRef": {}}
    for table, type_ in (("time_entry", "entry"), ("segment", "segment"), ("external_ref", "externalRef")):
        spec = TYPES[table][1]
        for r in after[table]:
            row = dict(r)
            if "entry_uid" in row:
                entry_uid = row.pop("entry_uid")
            if isinstance(row.get("plugin_fields"), (dict, list)):
                row["plugin_fields"] = json.dumps(row["plugin_fields"], ensure_ascii=False)
            fields = {n: m._to_wire(row.get(c), conv) for n, c, conv in spec}
            if table != "time_entry":
                fields["entryUid"] = entry_uid
            else:
                fields["tags"] = []
            out[type_][r["uid"]] = fields
    return out


class ReferenceCasesTest(unittest.TestCase):
    """The shared cases, through the reference as the app's repository has it."""

    def test_enough_cases(self):
        self.assertGreaterEqual(len(DOC["cases"]), 30)

    def test_cases(self):
        for case in DOC["cases"]:
            with self.subTest(case["name"]):
                before = copy.deepcopy(case["before"])
                got = m.normalize(m.migrate(before, case["zone"]))
                self.assertEqual(json.loads(json.dumps(got, sort_keys=True)),
                                 json.loads(json.dumps(case["after"], sort_keys=True)))
                self.assertEqual(before, case["before"], "migrate must not modify its input")

    def test_deterministic_and_unique_uids(self):
        for case in DOC["cases"]:
            a = m.migrate(case["before"], case["zone"])
            self.assertEqual(a, m.migrate(case["before"], case["zone"]))
            for table in ("time_entry", "external_ref"):
                uids = [r["uid"] for r in a[table]]
                self.assertEqual(len(uids), len(set(uids)), case["name"])

    def test_invariants(self):
        for case in DOC["cases"]:
            for e in m.migrate(case["before"], case["zone"])["time_entry"]:
                self.assertNotIn("todo_id", e)
                self.assertIn(e["status"], ("TODO", "RUNNING", "CLOSED"))
                self.assertTrue(0 <= e["priority"] <= 5)
                self.assertEqual(e["kind"] == "ABSENCE", e["absence_kind"] is not None)

    def test_helpers(self):
        priorities = [m.map_priority(p) for p in (None, 1, 2, 3, 4, 5, 6, 7, 0, -1)]
        self.assertEqual(priorities, [0, 5, 4, 3, 2, 1, 1, 1, 0, 0])
        self.assertEqual(m.classify_ref(" https://a.b/c "), "URL")
        self.assertEqual(m.absence_kind(" ferie "), "HOLIDAY")


class RecordCasesTest(unittest.TestCase):
    """The same cases through the library's adapter: the store's wire records in, the records of schema 3 out."""

    def test_cases(self):
        for case in DOC["cases"]:
            with self.subTest(case["name"]):
                got = m.migrate_records(records_of(case["before"]), case["zone"])
                want = expected_of(case["after"])
                for type_ in m.WRITES:
                    self.assertEqual(got[type_], want[type_], type_)

    def test_the_priority_scale_agrees_with_the_model(self):
        from activitytrack import model
        for p in (None, 1, 2, 3, 4, 5, 6, 99, 0, -1):
            self.assertEqual(m.map_priority(p), model.priority_of_legacy(p))


class StoreMigrationTest(unittest.TestCase):
    ZONE = "Europe/Rome"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "d.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        s = self.store
        pulled = s.pulled
        for type_, uid, fields in (
            ("client", "c1", {"name": "Acme", "archived": False}),
            ("todo", "t1", {"title": "Ticket", "refs": "INC1", "priority": 2, "done": False,
                            "planDate": "2026-11-12", "pluginFields": {"a": 1}, "createdUtcMs": 5,
                            "updatedUtcMs": 6, "kind": "TASK"}),
            ("todo", "t2", {"title": "Chiamare", "done": True, "doneUtcMs": 900, "createdUtcMs": 7,
                            "updatedUtcMs": 8}),
            ("entry", "e1", {"status": "TO_COMPLETE", "mode": None, "activity": "Rete", "clientUid": "c1",
                             "todoUid": "t1", "createdUtcMs": 10, "updatedUtcMs": 11, "needsReview": False,
                             "tags": ["x"], "zoneId": self.ZONE}),
            ("entry", "e2", {"status": "ACTIVE", "mode": "WORK", "activity": "Cavi", "clientUid": "c1",
                             "createdUtcMs": 12, "updatedUtcMs": 13, "needsReview": False, "zoneId": self.ZONE}),
            ("segment", "s1", {"entryUid": "e1", "kind": "WORK", "startUtcMs": 100, "endUtcMs": 400,
                               "zoneId": self.ZONE, "source": "TRACKED"}),
            ("segment", "s2", {"entryUid": "e2", "kind": "WORK", "startUtcMs": 500, "endUtcMs": None,
                               "zoneId": self.ZONE, "source": "TRACKED"}),
            ("absence", "a1", {"date": "2026-11-09", "kind": "FULL_DAY", "timeMin": None}),
            ("workSchedule", "w1", {"dayOfWeek": 1, "startMin": 540, "endMin": 1080}),
        ):
            pulled({"type": type_, "uid": uid, "rev": 3, "fields": fields})
        s.set_state("cursor", 77)
        # a store of the library before 1.0 has no model
        with s.transaction() as db:
            db.execute("DELETE FROM state WHERE key = 'model'")

    def reopen(self):
        self.store.close()
        old = os.environ.get("TZ")
        os.environ["TZ"] = self.ZONE
        self.addCleanup(lambda: os.environ.__setitem__("TZ", old) if old else os.environ.pop("TZ", None))
        self.store = Store(self.path)
        return self.store

    def test_a_fresh_store_has_the_model(self):
        fresh = Store(Path(self.dir.name) / "new.sqlite3")
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.get_state("model"), MODEL)

    def test_the_records_migrate_on_open(self):
        s = self.reopen()
        self.assertEqual(s.get_state("model"), MODEL)
        # a full realignment: from the beginning
        self.assertEqual(s.cursor, 0)
        self.assertEqual(s.all("todo"), [])
        self.assertEqual(s.all("absence"), [])
        e1, e2 = s.get("entry", "e1"), s.get("entry", "e2")
        # the open to-do with only closed entries stays its own TODO; the entry refers to it
        self.assertEqual((e1["status"], e1["kind"], e1["closedUtcMs"], e1["tags"]), ("CLOSED", "ACTIVITY", 400, ["x"]))
        self.assertEqual((e2["status"], e2["mode"], e2["closedUtcMs"]), ("RUNNING", "WORK", None))
        t1, t2 = s.get("entry", "t1"), s.get("entry", "t2")
        self.assertEqual((t1["status"], t1["activity"], t1["planDate"], t1["priority"], t1["pluginFields"]),
                         ("TODO", "Ticket", "2026-11-12", 4, {"a": 1}))
        self.assertEqual((t2["status"], t2["closedUtcMs"]), ("CLOSED", 900))
        refs = {(r["entryUid"], r["kind"], r["value"]) for r in s.all("externalRef")}
        self.assertEqual(refs, {("t1", "TICKET", "INC1"), ("e1", "UUID", "t1")})
        # the absence row became an activity with a segment on the Monday of the schedule
        (a,) = [e for e in s.all("entry") if e["kind"] == "ABSENCE"]
        self.assertEqual((a["status"], a["absenceKind"]), ("CLOSED", "ABSENCE"))
        (seg,) = [x for x in s.all("segment") if x["entryUid"] == a["uid"]]
        self.assertEqual((seg["kind"], seg["planned"], seg["source"]), ("ABSENCE", False, "MANUAL"))
        # the other records stay
        self.assertEqual(s.get("client", "c1")["name"], "Acme")
        self.assertEqual(len(s.all("workSchedule")), 1)
        # what is there is the server's view migrated, nothing to push
        self.assertEqual(s.pending_count(), 0)

    def test_it_runs_once(self):
        s = self.reopen()
        s.set_state("cursor", 5)
        before = sorted((r["uid"], json.dumps(r, sort_keys=True)) for r in s.all("entry"))
        s.close()
        s = Store(self.path)
        self.addCleanup(s.close)
        self.assertEqual(s.cursor, 5)
        self.assertEqual(sorted((r["uid"], json.dumps(r, sort_keys=True)) for r in s.all("entry")), before)

    def test_the_local_changes_not_yet_pushed_come_along(self):
        s = self.store
        # on an entry: a note, a stop with the old statuses; a new entry; a todo ticked off; a deleted entry
        s.write("entry", "e2", {"notes": "scritta", "status": "TO_COMPLETE", "mode": None}, 1000)
        s.write("segment", "s2", {"endUtcMs": 600}, 1000)
        s.write("entry", "new", {"status": "ACTIVE", "mode": "TRAVEL", "createdUtcMs": 700, "updatedUtcMs": 700,
                                 "zoneId": self.ZONE, "needsReview": False}, 1000)
        s.write("segment", "snew", {"entryUid": "new", "kind": "TRAVEL", "startUtcMs": 700, "zoneId": self.ZONE,
                                    "source": "TRACKED"}, 1000)
        s.write("todo", "t1", {"title": "Ticket 2"}, 1000)
        s.write("todo", "pending-todo", {"title": "Nuovo", "done": False, "createdUtcMs": 800}, 1000)
        s.delete("entry", "e1", 1000)
        s = self.reopen()
        e2 = s.get("entry", "e2")
        self.assertEqual((e2["status"], e2["notes"], e2["closedUtcMs"], e2["_pending"]),
                         ("CLOSED", "scritta", 600, True))
        new = s.get("entry", "new")
        self.assertEqual((new["status"], new["mode"], new["kind"]), ("RUNNING", "TRAVEL", "ACTIVITY"))
        self.assertEqual(s.get("entry", "t1")["activity"], "Ticket 2")
        self.assertEqual(s.get("entry", "pending-todo")["activity"], "Nuovo")
        self.assertIsNone(s.get("entry", "e1"))
        # what goes to the server is in the new shape; parents before children; no to-do or absence left
        pending = [(r["type"], r["uid"], r["op"]) for r in s.pending(100)]
        self.assertNotIn("todo", [p[0] for p in pending])
        self.assertIn(("entry", "e1", "delete"), pending)
        self.assertLess(pending.index(("entry", "new", "upsert")), pending.index(("segment", "snew", "upsert")))
        change = {c["uid"]: c for c in map(s.change, s.pending(100)) if c["type"] == "entry" and c["op"] == "upsert"}
        self.assertEqual(change["e2"]["fields"]["status"], "CLOSED")
        self.assertEqual(change["e2"]["fields"]["notes"], "scritta")
        self.assertNotIn("activity", change["e2"]["fields"])
        self.assertEqual(change["new"]["fields"]["kind"], "ACTIVITY")
        self.assertEqual(set(change["e2"]["times"]), set(change["e2"]["fields"]))

    def test_reset_keeps_the_model(self):
        self.reopen().reset()
        self.assertEqual(self.store.get_state("model"), MODEL)
        # records of schema 3 arrive; a new open does not migrate them
        self.store.pulled({"type": "entry", "uid": "n", "rev": 1, "fields": {"status": "TODO", "kind": "ACTIVITY"}})
        self.store.close()
        s = Store(self.path)
        self.addCleanup(s.close)
        self.assertEqual(s.get("entry", "n")["status"], "TODO")
