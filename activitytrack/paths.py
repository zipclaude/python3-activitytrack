# SPDX-License-Identifier: GPL-3.0-or-later
"""
The data folder all the interfaces share (one application, one device):
$XDG_DATA_HOME/activitytrack (~/.local/share/activitytrack) on Linux,
%APPDATA%\\activitytrack on Windows. The desktop's folder of 0.9.3-alfa1 …
0.9.4-beta1 (activitytrack-desktop) moves there at the first start.
"""

import os
import sys
from pathlib import Path

NAME = "activitytrack"
OLD_NAME = "activitytrack-desktop"


def _base():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def data_dir():
    """The shared folder; the old desktop's one is moved there when it is the only one."""
    base = _base()
    new, old = base / NAME, base / OLD_NAME
    if not new.exists() and old.is_dir():
        try:
            old.rename(new)
        except OSError:
            # another interface, started at the same time, moved it first
            if new.is_dir():
                return new
            return old
    return new
