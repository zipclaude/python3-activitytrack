# SPDX-License-Identifier: GPL-3.0-or-later
"""
Who this desktop is paired with, its local copy and its token: what the
interface and the sync share. Qt-free, so the tests need no display.
"""

import os
from pathlib import Path

from .net import Http
from .pairing import Account, pair
from .store import Store
from .vault import Vault


class Session:
    def __init__(self, folder, allow_http=False, use_keyring=True):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            self.folder.chmod(0o700)
        # http:// servers only for development (--dev)
        self.allow_http = allow_http
        self.store = Store(self.folder / "activitytrack.sqlite3")
        self.vault = Vault(self.folder, use_keyring=use_keyring)

    @property
    def account(self):
        text = self.store.get_state("account")
        return Account.from_json(text) if text else None

    def http(self):
        account = self.account
        if account is None:
            return None
        token = self.vault.get(account.device_id)
        if not token:
            return None
        return Http(account.server, account.pin, token, allow_http=self.allow_http)

    def pair(self, link):
        """Pairs with [link]'s server; another server or user starts from an empty copy."""
        account, token = pair(link, allow_http=self.allow_http)
        old = self.account
        if old is not None:
            self.vault.forget(old.device_id)
        if old is None or (old.server, old.username) != (account.server, account.username):
            self.store.reset()
        self.vault.put(account.device_id, token)
        self.store.set_state("account", account.to_json())
        self.store.set_state("revoked", False)
        self.store.set_state("incompatible", None)
        return account

    def unpair(self):
        """Forgets the server; the local copy stays until another pairing."""
        account = self.account
        if account is not None:
            self.vault.forget(account.device_id)
        self.store.set_state("account", None)
        self.store.set_state("revoked", False)
        self.store.set_state("incompatible", None)
