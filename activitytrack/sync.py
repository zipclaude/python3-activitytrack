# SPDX-License-Identifier: GPL-3.0-or-later
"""
One sync with ActivityTrack Web (server docs/SYNC.md §5, §7, §12; app
data/sync/SyncEngine.kt): push what changed here, pull what changed there,
a few rounds while either moves. The desktop has no data of its own before
pairing, so the first sync is just a pull from cursor 0. Network calls
happen between transactions, never inside one. Attachment files are not
transferred (docs/DESIGN.md §1).
"""

import json
import time
from dataclasses import dataclass, field

from . import wire
from .net import NetworkError

BATCH = 500
MAX_ROUNDS = 50


class SyncRevoked(Exception):
    """401: the server revoked this desktop; the sync stops until it is paired again."""


class SyncIncompatible(Exception):
    """426: the server does not speak this schema; [code] says which side to update."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    @classmethod
    def of(cls, body):
        try:
            code = json.loads(body).get("error")
        except (ValueError, AttributeError):
            code = None
        return cls(code if code in (wire.APP_TOO_OLD, wire.SERVER_TOO_OLD) else wire.APP_TOO_OLD)


class SyncFailed(Exception):
    pass


@dataclass
class Result:
    pushed: int = 0
    pulled: int = 0
    errors: list = field(default_factory=list)


def now_ms():
    return int(time.time() * 1000)


class SyncEngine:
    def __init__(self, store, http, clock=now_ms):
        self.store = store
        self.http = http
        self.clock = clock

    def head(self):
        """sync/head: the server's revision, to decide whether a pull is worth it."""
        return self._call("GET", "sync/head")

    def sync(self):
        result = Result()
        try:
            for _ in range(3):
                out = self._push(result.errors)
                came = self._pull(result.errors)
                result.pushed += out
                result.pulled += came
                # only refused changes left: another round would send them again
                if not self.store.has_pending() or (out == 0 and came == 0):
                    break
        except SyncRevoked:
            self._finished("revoked")
            raise
        except SyncIncompatible as e:
            self._finished(f"incompatible: {e.code}")
            raise
        except (SyncFailed, NetworkError) as e:
            self._finished(str(e) or "error")
            raise
        self._finished(result.errors[0] if result.errors else None, ok=True)
        return result

    def _finished(self, error, ok=False):
        if ok:
            self.store.set_state("last_sync", self.clock())
        self.store.set_state("last_error", error)

    def _call(self, method, path, body=None):
        reply = self.http.send(method, path, json.dumps(body) if body is not None else None)
        if reply.status == 401:
            raise SyncRevoked()
        if reply.status == 426:
            raise SyncIncompatible.of(reply.body)
        if reply.status != 200:
            raise SyncFailed(f"HTTP {reply.status} on {path}")
        try:
            answer = json.loads(reply.body)
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise SyncFailed(f"bad answer on {path}")
        server_time = answer.get("serverTime")
        if isinstance(server_time, int):
            # the server's clock minus ours, for the reminders and the "now" of commands
            self.store.set_state("offset", server_time - self.clock())
        return answer

    def _push(self, errors):
        taken = 0
        for _ in range(MAX_ROUNDS):
            batch = self.store.pending(BATCH)
            if not batch:
                return taken
            body = {"deviceTime": self.clock(), "changes": [self.store.change(r) for r in batch]}
            answer = self._call("POST", "sync/push", body)
            results = answer.get("results")
            if not isinstance(results, list):
                raise SyncFailed("push answer without results")
            if len(results) != len(batch):
                raise SyncFailed(f"push answer: {len(results)} results for {len(batch)} changes")
            failed = [e for e in (self.store.pushed(r, a) for r, a in zip(batch, results)) if e]
            errors += failed
            taken += len(batch) - len(failed)
            # only errors left: they stay for the next sync
            if len(failed) == len(batch):
                return taken
        return taken

    def _pull(self, errors):
        count = 0
        for _ in range(MAX_ROUNDS * 20):
            answer = self._call("GET", f"sync/pull?cursor={self.store.cursor}&limit={BATCH}")
            cursor = answer.get("cursor")
            if not isinstance(cursor, int):
                raise SyncFailed("pull answer without cursor")
            records = answer.get("records") or []
            with self.store.transaction():
                for r in records:
                    if isinstance(r, dict) and r.get("type") and r.get("uid"):
                        self.store.pulled(r)
                for g in answer.get("gone") or []:
                    if isinstance(g, dict) and g.get("type") and g.get("uid"):
                        self.store.gone(g["type"], g["uid"])
                self.store.set_state("cursor", cursor)
            count += len(records)
            if answer.get("more") is not True:
                return count
        return count


POLICY_EVERY_S = 300


class Runner:
    """
    One run of the sync as every interface does it: a check of sync/head
    unless [full] (a pull only when the server moved), the push and the
    pull, then the user's policy (server SYNC.md §8; asked again every 5
    minutes, as a policy change does not move the revision). The outcome
    is a state — ok, idle, offline, failed, revoked, incompatible,
    unpaired — and a detail; revoked and incompatible stay in the store.
    """

    def __init__(self, session):
        self.session = session
        self._policy_at = 0

    def run(self, full=True):
        http = self.session.http()
        if http is None:
            return "unpaired", ""
        store = self.session.store
        engine = SyncEngine(store, http)
        try:
            if not full and not store.has_pending():
                head = engine.head()
                if head.get("rev") == store.cursor:
                    store.set_state("incompatible", None)
                    changed = self.policy(http, force=False)
                    return ("ok" if changed else "idle"), ""
            result = engine.sync()
            store.set_state("incompatible", None)
            self.policy(http, force=True)
            return "ok", (result.errors[0] if result.errors else "")
        except SyncRevoked:
            store.set_state("revoked", True)
            return "revoked", ""
        except SyncIncompatible as e:
            store.set_state("incompatible", e.code)
            return "incompatible", e.code
        except NetworkError as e:
            return "offline", str(e)
        except SyncFailed as e:
            return "failed", str(e)
        except Exception as e:
            return "failed", f"{type(e).__name__}: {e}"

    def policy(self, http, force=True):
        """The user's policy; True when it changed. A text that is no policy keeps the last one."""
        from .policy import Policy
        now = time.monotonic()
        if not force and now - self._policy_at < POLICY_EVERY_S:
            return False
        self._policy_at = now
        reply = http.send("GET", "policy")
        if reply.status != 200:
            return False
        try:
            text = json.loads(reply.body).get("policy")
        except (ValueError, AttributeError):
            return False
        if text is not None and Policy.parse(text) is None:
            return False
        store = self.session.store
        if text == store.get_state("policy"):
            return False
        store.set_state("policy", text)
        return True
