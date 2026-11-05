# SPDX-License-Identifier: GPL-3.0-or-later
"""
ActivityTrack on the PC: the rules, the local copy and the sync shared by
its interfaces (activitytrack-desktop, activitytrack-cli, activitytrack-tui).
One application, whatever the interface: they share the data folder, the
pairing and the token.
"""

# aligned with the app, the web and the interfaces
__version__ = "0.9.5"

# who calls the server (headers, User-Agent): the interface sets it at start
_client = {"name": "ActivityTrack", "version": __version__}


def set_client(name, version):
    """The interface's name and version, as the server and GitHub see them."""
    _client.update(name=name, version=version)


def client():
    return dict(_client)
