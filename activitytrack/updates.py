# SPDX-License-Identifier: GPL-3.0-or-later
"""
Updates of the PC applications (activitytrack#1, server docs/SYNC.md §12.4). The only source is the paired
server, which relays the signed manifest in sync/head; the only signer trusted is the key whose fingerprint
came with the pairing link (`k`), so a PC paired by an older link, or to a server that does not sign, is
never offered anything. No upstream and no custom address here (those are the phone's).

    status()     what the stored manifest says: nothing to do, a newer release, why not
    check()      asks the server now, then status()
    download()   the package of the release to a folder, its sha256 checked

An installation made by the distribution's package manager keeps the check but is never offered the
download: the package manager updates it. The check itself is a setting (and a policy may fix it, as
`updateCheck` and `updateBetas` do); the channels followed are always prod plus, with the beta switch, beta.
"""

import re
import sys
import time
from dataclasses import dataclass

from . import client, manifest
from .manifest import ManifestError
from .versions import compare  # noqa: F401  (the interfaces order versions with it)

# Status.state
OFF, UNPAIRED, UNTRUSTED, NONE, AVAILABLE, ERROR = "off", "unpaired", "untrusted", "none", "available", "error"
DAY_S = 24 * 3600

CHECK, BETAS, ISSUED, MANIFEST, CHECKED = "updates.check", "updates.betas", "update.issued", "update.manifest", \
    "updates.checked"
PREFIX = "/api/v1/"
EXTENSIONS = {"deb": ".deb", "windows": ".exe", "tar": ".tar.gz"}


@dataclass(frozen=True)
class Status:
    state: str
    release: object = None
    # the package for this installation exists and no package manager is in charge of it
    downloadable: bool = False
    error: str = ""


def interface():
    """desktop, cli or tui: the interface the running process is (set_client)."""
    name = client()["name"].lower()
    for found in ("desktop", "cli", "tui"):
        if found in name:
            return found
    return None


def component():
    """The package this installation takes: desktop-deb, desktop-windows, desktop-tar, cli-deb, tui-deb;
    None for an interface with no package of its own for this installation."""
    which = interface()
    if which is None:
        return None
    if manifest.from_package_manager():
        return f"{which}-deb"
    if which == "desktop":
        return "desktop-windows" if sys.platform == "win32" else "desktop-tar"
    return None


class Updates:
    def __init__(self, session, runner, policy):
        """[policy]: a function giving the policy in force (it changes at every sync)."""
        self.session, self.runner, self.policy = session, runner, policy

    @property
    def version(self):
        return client()["version"]

    @property
    def store(self):
        return self.session.store

    # ---- the settings ----

    def settings(self):
        """(check, betas): the user's choice, the policy's when it fixes them; a pre-release follows the betas."""
        p = self.policy()
        check = p.setting("updateCheck", True)
        betas = p.setting("updateBetas", "-" in self.version)
        if not p.setting_fixed("updateCheck"):
            check = bool(self.store.get_state(CHECK, check))
        if not p.setting_fixed("updateBetas"):
            betas = bool(self.store.get_state(BETAS, betas))
        return check, betas

    def set_check(self, on):
        self.store.set_state(CHECK, bool(on))

    def set_betas(self, on):
        self.store.set_state(BETAS, bool(on))

    @property
    def channels(self):
        return ("prod", "beta") if self.settings()[1] else ("prod",)

    @property
    def checked(self):
        """Epoch seconds of the last time the server was asked, or 0."""
        return self.store.get_state(CHECKED, 0)

    def due(self, now):
        """True when the daily check is on and a day passed since the last one."""
        return self.settings()[0] and now - self.checked >= DAY_S

    # ---- what the server said ----

    def channels_for(self, requested=None):
        """The channels to follow: the setting's, or [requested] (one name or several) plus prod for
        this run only; a policy that fixes updateBetas off keeps the beta out whatever is asked."""
        if not requested:
            return self.channels
        asked = {requested} if isinstance(requested, str) else set(requested)
        p = self.policy()
        if p.setting_fixed("updateBetas") and not p.setting("updateBetas", False):
            asked.discard("beta")
        return ("prod", *sorted(asked - {"prod"}))

    def status(self, channels=None):
        """The newest release after this version, from the manifest stored at the last sync;
        [channels]: for this run instead of the setting (see channels_for)."""
        account = self.session.account
        if account is None:
            return Status(UNPAIRED)
        if not self.settings()[0]:
            return Status(OFF)
        if not account.update_key:
            return Status(UNTRUSTED, error="no key")
        text = self.store.get_state(MANIFEST)
        if not text:
            return Status(NONE)
        want = component()
        try:
            found, issued = self._find(text, account.update_key, want, channels)
            if found is None and want is not None:
                # a newer release without a package for this installation: said, not offered
                found, issued = self._find(text, account.update_key, None, channels)
                want = None
        except ManifestError as e:
            return Status(UNTRUSTED, error=str(e))
        self.store.set_state(ISSUED, issued)
        if found is None:
            return Status(NONE)
        return Status(AVAILABLE, found, downloadable=want is not None and not manifest.from_package_manager())

    def _find(self, text, key, want, channels=None):
        return manifest.check(text, [key], self.version, self.channels_for(channels), want,
                              self.store.get_state(ISSUED), server=True)

    def check(self, now=None, channels=None):
        """Asks the server now; the status after it (an unreachable server is an ERROR)."""
        if self.session.account is None:
            return Status(UNPAIRED)
        failed = self.runner.refresh_update()
        if failed:
            return Status(ERROR, error=failed)
        self.store.set_state(CHECKED, now if now is not None else time.time())
        return self.status(channels)

    def known_channels(self):
        """The channels the stored manifest has releases in (for a choice), prod first."""
        account = self.session.account
        text = self.store.get_state(MANIFEST)
        if account is None or not account.update_key or not text:
            return ["prod"]
        try:
            found = {r.channel for r in manifest.releases(manifest.verify(text, [account.update_key]), server=True)}
        except ManifestError:
            return ["prod"]
        return ["prod"] + sorted(found - {"prod"})

    # ---- the package ----

    def download(self, release, folder=None):
        """The package of [release] for this installation, in [folder] (default: the data folder's
        `updates`), its sha256 checked; the path. ManifestError when it does not match or there is none."""
        want = component()
        package = release.package(want) if want else None
        if package is None or manifest.from_package_manager():
            raise ManifestError("nothing to download for this installation")
        http = self.session.http()
        if http is None:
            raise ManifestError("not paired")
        folder = folder or self.session.folder / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        version = re.sub(r"[^A-Za-z0-9.+~_-]", "_", release.version)
        kind = package.component.rsplit("-", 1)[-1]
        dest = folder / f"activitytrack-{package.component}-{version}{EXTENSIONS.get(kind, '')}"

        def opener(url, timeout):
            return _Reply(self._open(http, url))

        return manifest.download(package, dest, opener=opener)

    @staticmethod
    def _open(http, url):
        if url.startswith(PREFIX):
            status, body = http.exchange("GET", url[len(PREFIX):])
            if status != 200:
                raise ManifestError(f"HTTP {status}")
            return body
        raise ManifestError("not a file of the server")


class _Reply:
    """What manifest.download reads: the body, in pieces."""

    def __init__(self, body):
        self.body, self.at = body, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        end = len(self.body) if n < 0 else self.at + n
        chunk, self.at = self.body[self.at:end], end
        return chunk
