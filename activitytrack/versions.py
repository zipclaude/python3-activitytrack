# SPDX-License-Identifier: GPL-3.0-or-later
"""The order of versions: "1.2.3", "1.2", "1.2.3.1" (a security patch), "1.2.3-beta-1" or "1.2.3-beta.1" (a pre-release
before its release; beta-9 before beta-10)."""

import re


def _key(text):
    text = text.strip()
    if text.startswith("v"):
        text = text[1:]
    main, _sep, pre = text.partition("-")
    try:
        numbers = [int(x) for x in main.split(".")]
    except ValueError:
        return [-1, -1, -1, -1], None
    return (numbers + [0, 0, 0, 0])[:4], pre or None


def _compare_pre(a, b):
    x, y = re.split(r"[.-]", a), re.split(r"[.-]", b)
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
