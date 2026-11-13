# SPDX-License-Identifier: GPL-3.0-or-later
"""
ActivityTrack on the PC: the rules, the local copy and the sync shared by
its interfaces (activitytrack-desktop, activitytrack-cli, activitytrack-tui).
One application, whatever the interface: they share the data folder, the
pairing and the token.
"""

# aligned with the app, the web and the interfaces
__version__ = "1.0.0-beta-1"
# the same version as PEP 440 spells it, for the Python package metadata ("1.0.0-alfa-1" -> "1.0.0a1")
__pep440__ = __version__.replace("-alfa-", "a").replace("-beta-", "b")

# who calls the server (headers, User-Agent): the interface sets it at start
_client = {"name": "ActivityTrack", "version": __version__}


def set_client(name, version):
    """The interface's name and version, as the server and GitLab see them."""
    _client.update(name=name, version=version)


def client():
    return dict(_client)
