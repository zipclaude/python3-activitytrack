# SPDX-License-Identifier: GPL-3.0-or-later
"""
Newer releases on GitLab (server docs/SYNC.md §12.4, app
domain/update/UpdateCheck.kt): the public releases of this repository,
a stable one or, for who subscribed to the betas, a beta too. GitLab has
no pre-releases: a version with a suffix (1.1.0-beta1) is a beta. Releases
dated in the future do not count. Once a day; the policy may fix both
settings (updateCheck, updateBetas).
"""

import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass

# the interfaces' repositories: each one checks its own releases
REPOSITORY = "infraconsulting/activitytrack-desktop"
DAY_S = 24 * 3600


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    beta: bool


def _key(text):
    text = text.strip()
    if text.startswith("v"):
        text = text[1:]
    main, _sep, pre = text.partition("-")
    try:
        numbers = [int(x) for x in main.split(".")]
    except ValueError:
        return [-1, -1, -1], None
    return (numbers + [0, 0, 0])[:3], pre or None


def _compare_pre(a, b):
    x, y = a.split("."), b.split(".")
    for p, q in zip(x, y):
        if p.isdigit() and q.isdigit():
            c = (int(p) > int(q)) - (int(p) < int(q))
        else:
            c = (p > q) - (p < q)
        if c:
            return c
    return (len(x) > len(y)) - (len(x) < len(y))


def compare(a, b):
    """"1.2.3", "1.2", "1.2.3-beta.1" (a pre-release before its release); anything else sorts first."""
    (xn, xp), (yn, yp) = _key(a), _key(b)
    if xn != yn:
        return (xn > yn) - (xn < yn)
    if xp == yp:
        return 0
    if xp is None:
        return 1
    if yp is None:
        return -1
    return _compare_pre(xp, yp)


def newer(releases_json, current, betas):
    """The newest release after [current] the user wants, or None."""
    try:
        data = json.loads(releases_json)
    except ValueError:
        return None
    best = None
    for o in data if isinstance(data, list) else ():
        if not isinstance(o, dict) or o.get("upcoming_release") or not o.get("tag_name"):
            continue
        version = o["tag_name"].removeprefix("v")
        beta = "-" in version
        if beta and not betas:
            continue
        links = o.get("_links")
        r = Release(version, (links.get("self") if isinstance(links, dict) else None) or "", beta)
        if compare(r.version, current) > 0 and (best is None or compare(r.version, best.version) > 0):
            best = r
    return best


def fetch(repository=REPOSITORY, timeout=20):
    """The releases' JSON of [repository] from GitLab (no account needed)."""
    from . import client
    me = client()
    url = f"https://gitlab.com/api/v4/projects/{urllib.parse.quote(repository, safe='')}/releases?per_page=20"
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": f"{me['name']}/{me['version']}"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")
