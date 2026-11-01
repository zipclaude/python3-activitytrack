# SPDX-License-Identifier: GPL-3.0-or-later
"""
The device token, out of the database: in the system's secret store
(Secret Service — KWallet, GNOME Keyring — on Linux, Credential Manager
on Windows) through `keyring`; without one, a file readable only by the
user, and the settings say so.
"""

import os
from pathlib import Path

SERVICE = "ActivityTrack"
# the name before the library (0.9.3-alfa1 … 0.9.4-beta1): read, then moved
OLD_SERVICE = "ActivityTrack Desktop"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    # the "fail" and "null" backends store nothing
    if isinstance(backend, fail.Keyring) or getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


class Vault:
    def __init__(self, folder, use_keyring=True):
        self.file = Path(folder) / "token"
        self.keyring = _keyring() if use_keyring else None

    @property
    def secure(self):
        """The token is in the system's secret store, not in a file."""
        return self.keyring is not None

    def get(self, device_id):
        if self.keyring is not None:
            try:
                token = self.keyring.get_password(SERVICE, device_id)
                if token:
                    return token
                token = self.keyring.get_password(OLD_SERVICE, device_id)
                if token:
                    # stored by the desktop before the library: under the shared name from now on
                    self.keyring.set_password(SERVICE, device_id, token)
                    self.keyring.delete_password(OLD_SERVICE, device_id)
                    return token
            except Exception:
                pass
        try:
            name, _, token = self.file.read_text().partition("\n")
        except OSError:
            return None
        return token.strip() if name == device_id else None

    def put(self, device_id, token):
        if self.keyring is not None:
            try:
                self.keyring.set_password(SERVICE, device_id, token)
                self.file.unlink(missing_ok=True)
                return
            except Exception:
                self.keyring = None
        self.file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"{device_id}\n{token}\n")

    def forget(self, device_id):
        if self.keyring is not None:
            try:
                self.keyring.delete_password(SERVICE, device_id)
            except Exception:
                pass
        self.file.unlink(missing_ok=True)
