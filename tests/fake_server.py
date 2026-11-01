# SPDX-License-Identifier: GPL-3.0-or-later
"""A server in memory with the protocol's rules (SYNC.md §3–§6), for the engine's tests."""

import json
from urllib.parse import parse_qs, urlsplit

from activitytrack.net import Reply


class FakeServer:
    def __init__(self, now=lambda: 1_000_000):
        self.now = now
        self.rev = 0
        # (type, uid) -> {"rev", "deleted", "fields", "clock"}
        self.records = {}
        self.gone = []
        self.refuse = set()
        self.status = None
        self.requests = []

    def put(self, type_, uid, fields, at=0, central=False):
        """A change made on the server (the web, another phone)."""
        r = self.records.setdefault((type_, uid), {"deleted": False, "fields": {}, "clock": {}, "central": central})
        r["fields"].update(fields)
        r["clock"].update({k: at for k in fields})
        self.rev += 1
        r["rev"] = self.rev

    def leave(self, type_, uid):
        self.rev += 1
        self.gone.append((self.rev, type_, uid))

    def wire(self, key):
        r = self.records[key]
        out = {"type": key[0], "uid": key[1], "rev": r["rev"], "deleted": r["deleted"], "central": r["central"]}
        if not r["deleted"]:
            out["fields"] = dict(r["fields"])
        return out

    def send(self, method, path, body=None):
        self.requests.append((method, path, json.loads(body) if body else None))
        if self.status is not None:
            return Reply(self.status, json.dumps({"error": "app_too_old"}) if self.status == 426 else "{}")
        u = urlsplit(path)
        if u.path == "sync/head":
            return self._ok({"rev": self.rev, "serverTime": self.now(), "filesMaxMb": 25})
        if u.path == "sync/pull":
            q = parse_qs(u.query)
            return self._ok(self._pull(int(q["cursor"][0]), int(q["limit"][0])))
        if u.path == "sync/push":
            return self._ok(self._push(json.loads(body)))
        return Reply(404, "{}")

    def _ok(self, answer):
        return Reply(200, json.dumps(answer))

    def _pull(self, cursor, limit):
        batch = [(r["rev"], ("r", k)) for k, r in self.records.items() if r["rev"] > cursor]
        batch += [(rev, ("g", t, u)) for rev, t, u in self.gone if rev > cursor]
        batch.sort()
        more = len(batch) > limit
        batch = batch[:limit]
        return {
            "serverTime": self.now(),
            "cursor": batch[-1][0] if more else max(self.rev, cursor),
            "more": more,
            "records": [self.wire(x[1]) for _, x in batch if x[0] == "r"],
            "gone": [{"type": x[1], "uid": x[2]} for _, x in batch if x[0] == "g"],
        }

    def _push(self, body):
        results = []
        for c in body["changes"]:
            key = (c["type"], c["uid"])
            if c["type"] in self.refuse:
                results.append({"uid": c["uid"], "status": "error", "error": "permission", "detail": "locked"})
                continue
            r = self.records.get(key)
            if c["op"] == "delete":
                if r is None:
                    results.append({"uid": c["uid"], "status": "applied", "record": None})
                    continue
                r.update(deleted=True, fields={}, clock={})
                self.rev += 1
                r["rev"] = self.rev
                results.append({"uid": c["uid"], "status": "applied", "record": self.wire(key)})
                continue
            if r is not None and r["deleted"]:
                results.append({"uid": c["uid"], "status": "conflict", "record": self.wire(key)})
                continue
            if r is None:
                r = self.records[key] = {"deleted": False, "fields": {}, "clock": {}, "central": False}
            refused = False
            for k, v in c["fields"].items():
                t = c["times"][k]
                if t > r["clock"].get(k, -1):
                    r["fields"][k] = v
                    r["clock"][k] = t
                else:
                    refused = True
            self.rev += 1
            r["rev"] = self.rev
            results.append({"uid": c["uid"], "status": "conflict" if refused else "applied", "record": self.wire(key)})
        return {"serverTime": self.now(), "results": results}
