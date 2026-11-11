# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pairing with ActivityTrack Web, as the phone (server docs/SYNC.md §9,
app domain/sync/Pairing.kt): the web shows a one-time link

    activitytrack://pair?v=1&u=<server>&c=<code>[&f=<pin>][&k=<key>]

pasted here; `f` is the base64 SHA-256 of the server's public key, present
when its certificate is self-signed. `k` is the SHA-256 fingerprint (64 hex
digits) of the key with which the server signs the update manifests: the
only one this PC trusts for them (activitytrack#1). A link without `k` pins
nothing, and the server's updates are never trusted. POST api/v1/pair gives
the token.
"""

import base64
import binascii
import json
import platform
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from . import client
from .net import Http, NetworkError, PinMismatch, local_host

NOT_A_LINK, VERSION, NOT_HTTPS, BAD_PIN = "not_a_link", "version", "not_https", "bad_pin"
# http:// with --dev, to a server that is not local
NOT_LOCAL = "not_local"
INVALID_CODE, USER_DISABLED, NETWORK, CERTIFICATE = "invalid_code", "user_disabled", "network", "certificate"
SERVER, APP_TOO_OLD, SERVER_TOO_OLD = "server", "app_too_old", "server_too_old"
UPGRADE_REQUIRED = "upgrade_required"


class PairError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class PairLink:
    server: str
    code: str
    pin: str | None
    update_key: str | None = None

    @classmethod
    def parse(cls, text, allow_http=False):
        """[allow_http]: development only, for a local server (net.local_host)."""
        uri = urlsplit(text.strip())
        if uri.scheme != "activitytrack" or uri.netloc != "pair":
            raise PairError(NOT_A_LINK)
        q = {k: v[0] for k, v in parse_qs(uri.query).items()}
        if q.get("v") != "1":
            raise PairError(VERSION)
        server = q.get("u", "").rstrip("/")
        code = q.get("c", "").strip()
        if not server or not code:
            raise PairError(NOT_A_LINK)
        s = urlsplit(server)
        if not s.hostname or not (s.scheme == "https" or (allow_http and s.scheme == "http")):
            raise PairError(NOT_HTTPS)
        if s.scheme == "http" and not local_host(s.hostname):
            raise PairError(NOT_LOCAL)
        pin = q.get("f", "").strip() or None
        if pin is not None:
            try:
                ok = len(base64.b64decode(pin, validate=True)) == 32
            except binascii.Error:
                ok = False
            if not ok:
                raise PairError(BAD_PIN)
        update_key = q.get("k", "").strip() or None
        if update_key is not None:
            update_key = update_key.lower()
            if len(update_key) != 64 or any(c not in "0123456789abcdef" for c in update_key):
                raise PairError(BAD_PIN)
        return cls(server, code, pin, update_key)


@dataclass(frozen=True)
class Account:
    """Who this desktop is paired with; the token is kept apart (secrets.py)."""
    server: str
    pin: str | None
    device_id: str
    username: str
    name: str
    profile: str
    org: str
    # SHA-256 fingerprint of the key that signs the server's update manifests (the link's `k`)
    update_key: str | None = None

    def to_json(self):
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, text):
        return cls(**json.loads(text))


def device_name():
    return socket.gethostname()[:100] or "PC"


def model():
    return f"{platform.system()} {platform.release()}"[:100]


def pair(link, allow_http=False):
    """Redeems [link]'s code: (Account, token)."""
    http = Http(link.server, link.pin, allow_http=allow_http)
    body = {
        "code": link.code, "deviceName": device_name(), "model": model(),
        # the PC is one device, whatever its interface (desktop, cli, tui)
        "appVersion": client()["version"], "kind": "desktop",
    }
    try:
        reply = http.send("POST", "pair", json.dumps(body))
    except PinMismatch as e:
        raise PairError(CERTIFICATE, str(e)) from e
    except NetworkError as e:
        raise PairError(CERTIFICATE if e.certificate else NETWORK, str(e)) from e
    try:
        answer = json.loads(reply.body)
    except ValueError:
        answer = {}
    if answer.get("error") == UPGRADE_REQUIRED:
        raise PairError(UPGRADE_REQUIRED)
    if reply.status == 426:
        raise PairError(SERVER_TOO_OLD if answer.get("error") == "server_too_old" else APP_TOO_OLD)
    if reply.status == 400 and answer.get("error") == "invalid_code":
        raise PairError(INVALID_CODE)
    if reply.status == 403:
        raise PairError(USER_DISABLED)
    if reply.status != 200 or not answer.get("token"):
        raise PairError(SERVER, f"HTTP {reply.status}")
    user = answer.get("user") or {}
    account = Account(
        server=link.server, pin=link.pin, update_key=link.update_key, device_id=answer["deviceId"],
        username=user.get("username", ""), name=user.get("name", ""), profile=user.get("profile", ""),
        org=(answer.get("org") or {}).get("name", ""),
    )
    return account, answer["token"]
