# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import activitytrack
from activitytrack import manifest, updates
from activitytrack.pairing import Account
from activitytrack.policy import Policy
from activitytrack.session import Session
from activitytrack.sync import Runner

from .test_manifest import make_key, payload

SHA = hashlib.sha256(b"package").hexdigest()
FILE = "/api/v1/update/files/" + SHA


def releases(component="desktop-tar", url=FILE, issued="2026-11-07T10:00:00Z"):
    def rel(version, channel):
        return {"version": version, "channel": channel, "published": "2026-11-05", "notes_url": "",
                "packages": [{"component": component, "url": url, "sha256": SHA, "size": 7}]}
    return payload(issued=issued, releases=[rel("0.9.9", "prod"), rel("1.0.0-beta1", "beta"), rel("0.9.8", "prod")])


class Down:
    """The paired server's http: the files it keeps."""
    def __init__(self, body=b"package", status=200):
        self.body, self.status, self.asked = body, status, []

    def exchange(self, method, path, body=None, content_type=None, headers=None):
        self.asked.append((method, path))
        return self.status, self.body


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.session = Session(self.dir.name, use_keyring=False)
        self.key, self.chain, self.fp = make_key("ec")
        self.policy = Policy()
        self.refresh = ""
        self.runner = mock.Mock(refresh_update=lambda: self.refresh)
        self.u = updates.Updates(self.session, self.runner, lambda: self.policy)
        activitytrack.set_client("ActivityTrackDesktop", "0.9.8")
        for patch in (mock.patch.object(manifest, "from_package_manager", return_value=False),
                      mock.patch("sys.platform", "linux")):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        activitytrack.set_client("ActivityTrack", activitytrack.__version__)
        self.session.store.close()
        self.dir.cleanup()

    def pair(self, key="fp"):
        self.session.store.set_state("account", Account(
            "https://at.example.com", None, "d1", "anna", "Anna", "user", "X",
            self.fp if key == "fp" else key).to_json())

    def relay(self, **kw):
        self.session.store.set_state("update.manifest", manifest.sign(releases(**kw), self.key, self.chain))

    def test_version_order(self):
        ordered = ["0.9.2", "1.0.0-beta.2", "1.0.0-beta.10", "1.0.0", "1.0.1", "1.1", "1.10.0"]
        self.assertEqual(ordered, sorted(reversed(ordered), key=functools.cmp_to_key(updates.compare)))
        self.assertEqual(0, updates.compare("1.1", "1.1.0"))

    def test_nothing_without_pairing_a_key_or_a_manifest(self):
        self.assertEqual(updates.UNPAIRED, self.u.status().state)
        self.pair(key=None)
        self.relay()
        self.assertEqual(updates.UNTRUSTED, self.u.status().state)
        self.pair()
        self.session.store.set_state("update.manifest", None)
        self.assertEqual(updates.NONE, self.u.status().state)

    def test_a_manifest_of_the_pinned_server_is_offered(self):
        self.pair()
        self.relay()
        s = self.u.status()
        self.assertEqual((updates.AVAILABLE, "0.9.9", True), (s.state, s.release.version, s.downloadable))

    def test_only_the_pinned_key_counts(self):
        other, chain, _fp = make_key("ec")
        self.pair()
        self.session.store.set_state("update.manifest", manifest.sign(releases(), other, chain))
        self.assertEqual(updates.UNTRUSTED, self.u.status().state)
        self.pair(key="ab" * 32)
        self.relay()
        self.assertEqual(updates.UNTRUSTED, self.u.status().state)

    def test_the_setting_and_the_policy(self):
        self.pair()
        self.relay()
        self.assertEqual(("prod",), self.u.channels)
        self.u.set_betas(True)
        self.assertEqual("1.0.0-beta1", self.u.status().release.version)
        self.u.set_check(False)
        self.assertEqual(updates.OFF, self.u.status().state)
        self.policy = Policy({"settings": {"updateCheck": {"value": True, "locked": True},
                                           "updateBetas": {"value": False, "locked": True}}})
        self.assertEqual(("prod",), self.u.channels)
        self.assertEqual("0.9.9", self.u.status().release.version)

    def test_channels_for_one_run(self):
        self.pair()
        self.relay()
        self.assertEqual("0.9.9", self.u.status().release.version)
        self.assertEqual("1.0.0-beta1", self.u.status(channels="beta").release.version)
        self.assertEqual(("prod", "beta", "dev"), self.u.channels_for(["dev", "beta"]))
        # the run's choice is not kept
        self.assertEqual(("prod",), self.u.channels)
        self.policy = Policy({"settings": {"updateBetas": {"value": False, "locked": True}}})
        self.assertEqual(("prod", "dev"), self.u.channels_for(["beta", "dev"]))
        self.assertEqual("0.9.9", self.u.status(channels="beta").release.version)

    def test_a_beta_follows_the_betas_by_default(self):
        activitytrack.set_client("ActivityTrackDesktop", "0.9.9-beta2")
        self.assertEqual(("prod", "beta"), self.u.channels)

    def test_an_older_manifest_is_a_replay(self):
        self.pair()
        self.relay(issued="2026-11-07T10:00:00Z")
        self.assertEqual(updates.AVAILABLE, self.u.status().state)
        self.relay(issued="2026-11-06T10:00:00Z")
        self.assertEqual(updates.UNTRUSTED, self.u.status().state)
        self.relay(issued="2026-11-08T10:00:00Z")
        self.assertEqual(updates.AVAILABLE, self.u.status().state)

    def test_a_release_without_a_package_for_this_installation_is_said_not_offered(self):
        self.pair()
        self.relay(component="desktop-windows")
        s = self.u.status()
        self.assertEqual((updates.AVAILABLE, False), (s.state, s.downloadable))

    def test_an_installation_of_the_package_manager_is_never_offered_the_download(self):
        self.pair()
        self.relay(component="desktop-deb")
        with mock.patch.object(manifest, "from_package_manager", return_value=True):
            s = self.u.status()
            self.assertEqual((updates.AVAILABLE, False), (s.state, s.downloadable))
            with self.assertRaises(manifest.ManifestError):
                self.u.download(s.release, Path(self.dir.name))

    def test_components_of_the_interfaces(self):
        for name, want in (("ActivityTrackDesktop", "desktop-tar"), ("ActivityTrackCli", None), ("Other", None)):
            activitytrack.set_client(name, "0.9.8")
            self.assertEqual(want, updates.component(), name)
        with mock.patch.object(manifest, "from_package_manager", return_value=True):
            for name, want in (("ActivityTrackDesktop", "desktop-deb"), ("ActivityTrackCli", "cli-deb"),
                               ("ActivityTrackTui", "tui-deb")):
                activitytrack.set_client(name, "0.9.8")
                self.assertEqual(want, updates.component(), name)

    def test_check_asks_the_server(self):
        self.pair()
        self.relay()
        self.refresh = "no route"
        self.assertEqual((updates.ERROR, "no route"), (self.u.check().state, self.u.check().error))
        self.assertEqual(0, self.u.checked)
        self.refresh = ""
        self.assertEqual(updates.AVAILABLE, self.u.check(now=5_000_000).state)
        self.assertEqual(5_000_000, self.u.checked)
        self.assertFalse(self.u.due(5_000_000 + 3600))
        self.assertTrue(self.u.due(5_000_000 + updates.DAY_S))

    def test_channels_of_the_manifest(self):
        self.pair()
        self.assertEqual(["prod"], self.u.known_channels())
        self.relay()
        self.assertEqual(["prod", "beta"], self.u.known_channels())

    def test_the_package_of_the_server_is_downloaded_and_checked(self):
        self.pair()
        self.relay()
        release = self.u.status().release
        down = Down()
        with mock.patch.object(self.session, "http", return_value=down):
            path = self.u.download(release, Path(self.dir.name) / "out")
        self.assertEqual(b"package", path.read_bytes())
        self.assertEqual("activitytrack-desktop-tar-0.9.9.tar.gz", path.name)
        self.assertEqual([("GET", "update/files/" + SHA)], down.asked)
        with mock.patch.object(self.session, "http", return_value=Down(b"other")):
            with self.assertRaises(manifest.ManifestError):
                self.u.download(release, Path(self.dir.name) / "out2")
        self.assertEqual([], list((Path(self.dir.name) / "out2").iterdir()))
        with mock.patch.object(self.session, "http", return_value=Down(status=404)):
            with self.assertRaises(manifest.ManifestError):
                self.u.download(release, Path(self.dir.name) / "out3")

    def test_a_file_of_the_server_only_in_the_servers_manifest(self):
        # a file of the server is a package only in the manifest the server relayed, and only its own
        self.assertEqual([], manifest.releases(releases(), server=False))
        self.assertEqual(3, len(manifest.releases(releases(), server=True)))
        self.assertEqual([], manifest.releases(releases(url="/api/v1/update/files/../x"), server=True))
        self.assertEqual([], manifest.releases(releases(url="/other"), server=True))
        with self.assertRaises(manifest.ManifestError):
            manifest.download(manifest.Package("a", FILE, SHA, 1), Path(self.dir.name) / "x")

    def test_what_the_screens_say(self):
        from activitytrack import texts
        self.pair()
        self.relay()
        self.assertIn("0.9.9", texts.update_state(self.u.status(), "0.9.8"))
        self.assertIn("0.9.8", texts.update_state(updates.Status(updates.NONE), "0.9.8"))
        for state in (updates.OFF, updates.UNPAIRED, updates.UNTRUSTED, updates.ERROR):
            self.assertTrue(texts.update_state(updates.Status(state, error="x"), "0.9.8"), state)

    def test_the_runner_keeps_what_the_server_relays(self):
        self.assertTrue(Runner(self.session).update({"update": "x"}) is None)
        self.assertEqual("x", self.session.store.get_state("update.manifest"))


if __name__ == "__main__":
    unittest.main()
