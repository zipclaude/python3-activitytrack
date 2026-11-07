# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import activitytrack
from activitytrack import manifest, updates
from activitytrack.net import NetworkError
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
        self.body, self.status, self.asked, self.limits = body, status, [], []

    def exchange(self, method, path, body=None, content_type=None, headers=None, max_bytes=None):
        self.asked.append((method, path))
        self.limits.append(max_bytes)
        if max_bytes is not None and len(self.body) > max_bytes:
            raise NetworkError(f"answer longer than {max_bytes} bytes")
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
        self.relay(component="desktop-deb13")
        with mock.patch.object(manifest, "from_package_manager", return_value=True), \
                mock.patch.object(updates, "distro", return_value="deb13"):
            s = self.u.status()
            self.assertEqual((updates.AVAILABLE, False), (s.state, s.downloadable))
            with self.assertRaises(manifest.ManifestError):
                self.u.download(s.release, Path(self.dir.name))

    def test_components_of_the_interfaces(self):
        # not installed by a package manager: the tar of the interface
        for name, want in (("ActivityTrackDesktop", "desktop-tar"), ("ActivityTrackCli", "cli-tar"),
                           ("ActivityTrackTui", "tui-tar"), ("Other", None)):
            activitytrack.set_client(name, "0.9.8")
            self.assertEqual(want, updates.component(), name)
        with mock.patch("sys.platform", "win32"):
            activitytrack.set_client("ActivityTrackCli", "0.9.8")
            self.assertEqual("cli-windows", updates.component())
        # a package of the distribution: named by interface and distribution
        with mock.patch.object(manifest, "from_package_manager", return_value=True):
            for suffix in ("deb13", "deb14", "ubuntu24.04", "ubuntu26.04"):
                with mock.patch.object(updates, "distro", return_value=suffix):
                    for name, which in (("ActivityTrackDesktop", "desktop"), ("ActivityTrackCli", "cli"),
                                        ("ActivityTrackTui", "tui")):
                        activitytrack.set_client(name, "0.9.8")
                        self.assertEqual(f"{which}-{suffix}", updates.component(), name)
            # another distribution: no package of ours, no download (the version is still shown)
            with mock.patch.object(updates, "distro", return_value=None):
                self.assertIsNone(updates.component())

    def test_the_distribution_from_os_release(self):
        def release(text):
            path = Path(self.dir.name) / "os-release"
            path.write_text(text)
            return updates.distro(str(path))
        self.assertEqual("deb13", release('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\nVERSION_ID="13"\n'))
        # testing has no VERSION_ID, only the codename
        self.assertEqual("deb14", release("ID=debian\nVERSION_CODENAME=forky\n"))
        self.assertEqual("ubuntu24.04", release('ID=ubuntu\nVERSION_ID="24.04"\nID_LIKE=debian\n'))
        self.assertEqual("ubuntu26.04", release("ID=ubuntu\nVERSION_ID=26.04\n"))
        # derivatives and others are not guessed
        self.assertIsNone(release('ID=linuxmint\nVERSION_ID="22"\nID_LIKE="ubuntu debian"\n'))
        self.assertIsNone(release("ID=debian\nVERSION_ID=12\n"))
        self.assertIsNone(updates.distro(str(Path(self.dir.name) / "missing")))

    def test_the_name_of_the_file_follows_the_component(self):
        for name, want in (("desktop-deb13", ".deb"), ("cli-ubuntu24.04", ".deb"), ("tui-tar", ".tar.gz"),
                           ("desktop-windows", ".exe"), ("cli-windows", ".zip")):
            self.assertEqual(want, updates._extension(name), name)

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

    def test_check_without_a_pairing_does_not_ask(self):
        self.refresh = "must not be asked"
        self.assertEqual(updates.UNPAIRED, self.u.check().state)

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

    def test_the_server_cannot_send_more_than_the_manifest_declares(self):
        self.pair()
        self.relay()
        release = self.u.status().release
        # the manifest declares 7 bytes ("package")
        down = Down(body=b"package, and then some")
        with mock.patch.object(self.session, "http", return_value=down):
            with self.assertRaises(manifest.ManifestError) as e:
                self.u.download(release, Path(self.dir.name) / "out")
        self.assertIn("longer", str(e.exception))
        self.assertEqual([7], down.limits)
        self.assertEqual([], list((Path(self.dir.name) / "out").iterdir()))
        # shorter than declared: refused too
        with mock.patch.object(self.session, "http", return_value=Down(body=b"pack")):
            with self.assertRaises(manifest.ManifestError):
                self.u.download(release, Path(self.dir.name) / "out")

    def test_a_package_without_a_size_is_held_to_the_cap(self):
        self.pair()
        p = releases()
        for r in p["releases"]:
            del r["packages"][0]["size"]
        self.session.store.set_state("update.manifest", manifest.sign(p, self.key, self.chain))
        release = self.u.status().release
        down = Down()
        with mock.patch.object(self.session, "http", return_value=down):
            self.u.download(release, Path(self.dir.name) / "out")
        self.assertEqual([manifest.MAX_DOWNLOAD], down.limits)

    def test_a_manifest_with_odd_times_is_not_a_crash(self):
        self.pair()
        # the issue time of the manifest without an offset, the stored one with: still compared
        self.session.store.set_state("update.issued", "2026-11-06T20:00:00+02:00")
        self.session.store.set_state("update.manifest", manifest.sign(releases(issued="2026-11-07"),
                                                                      self.key, self.chain))
        self.assertEqual(updates.AVAILABLE, self.u.status().state)
        self.session.store.set_state("update.issued", "2026-11-08")
        self.assertEqual(updates.UNTRUSTED, self.u.status().state)

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
