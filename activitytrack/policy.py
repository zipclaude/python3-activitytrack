# SPDX-License-Identifier: GPL-3.0-or-later
"""
The user's policy, from ActivityTrack Web (`GET api/v1/policy`, server
docs/SYNC.md §8; the keys in the app's docs/POLICY.md). The desktop reads
the keys it uses and ignores the others (docs/DESIGN.md §5.3): a phone-only
key never makes it refuse a policy. The policy comes over the paired,
authenticated connection; its signature, there for the phone's file
policies, is not checked here.
"""

import json
from dataclasses import dataclass

FORMAT = "activitytrack-policy"

# fields the policy may never hide, disable or make optional (POLICY.md §5)
ESSENTIAL = {"client", "description", "times"}


@dataclass(frozen=True)
class FieldRule:
    visible: bool = True
    enabled: bool = True
    required: bool = False
    label: str | None = None


class Policy:
    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}

    @classmethod
    def parse(cls, text):
        """The policy of [text]; None when it is not a policy (kept: the previous one)."""
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("schema") != 1:
            return None
        return cls(data)

    def _section(self, name):
        s = self.data.get(name)
        return s if isinstance(s, dict) else {}

    def feature(self, name):
        value = self._section("features").get(name, True)
        return value if isinstance(value, bool) else True

    def field(self, name, language="it"):
        f = self._section("fields").get(name)
        if not isinstance(f, dict):
            return FieldRule()
        labels = f.get("label") if isinstance(f.get("label"), dict) else {}
        label = labels.get(language) or labels.get("en")
        if name in ESSENTIAL:
            return FieldRule(label=label)
        visible = f.get("visible") is not False
        enabled = f.get("enabled") is not False
        # a hidden or disabled field cannot be required
        required = f.get("required") is True and visible and enabled
        return FieldRule(visible, enabled, required, label)

    def setting(self, name, default):
        """The policy's value of a setting (locked, hidden or only the starting value), else [default]."""
        s = self._section("settings").get(name)
        if isinstance(s, dict):
            s = s.get("value")
        return default if s is None or type(s) is not type(default) else s

    def setting_fixed(self, name):
        s = self._section("settings").get(name)
        return isinstance(s, dict) and bool(s.get("locked") or s.get("hidden"))

    def behaviour(self, name, default):
        b = self._section("behaviours").get(name)
        return default if b is None or type(b) is not type(default) else b

    @property
    def todos_only(self):
        """Activities start only from to-dos (`entries.source: todosOnly`)."""
        return self._section("entries").get("source") == "todosOnly"

    @property
    def language(self):
        """The interface's language the policy forces (ui.language), or None."""
        value = self._section("ui").get("language")
        return value if value in ("it", "en", "de", "es") else None

    @property
    def master_free(self):
        """New companies, clients and roles may be typed (`masterData.source: user`)."""
        return self._section("masterData").get("source", "user") == "user"


def stored(store):
    """The policy last received, kept in the store's state."""
    return Policy.parse(store.get_state("policy")) or Policy()
