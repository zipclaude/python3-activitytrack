# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import datetime
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from activitytrack import manifest


def make_key(kind, cn="Test signer"):
    key = ec.generate_private_key(ec.SECP256R1()) if kind == "ec" else rsa.generate_private_key(65537, 2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    chain = cert.public_bytes(serialization.Encoding.PEM)
    return pem, chain, manifest.fingerprint(cert.public_bytes(serialization.Encoding.DER))


SHA = "ab" * 32


def payload(issued="2026-11-07T10:00:00Z", **kw):
    def rel(version, channel, **extra):
        return {"version": version, "channel": channel, "published": "2026-11-05", "notes_url": "https://example.org/n",
                "packages": [{"component": "desktop-deb", "url": "https://example.org/d.deb", "sha256": SHA,
                              "size": 3}], **extra}
    releases = [rel("0.9.9", "prod"), rel("1.0.0-beta1", "beta"), rel("0.9.8", "prod")]
    return {"issued": issued, "releases": releases, **kw}


def resign(text, edit):
    """The signed document with its payload changed (the signature no longer fits)."""
    doc = json.loads(text)
    body = json.loads(base64.urlsafe_b64decode(doc["payload"] + "=="))
    edit(body)
    doc["payload"] = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
    return json.dumps(doc)


class _Body:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        data, self.data = self.data, b""
        return data


class ManifestTest(unittest.TestCase):
    def test_both_algorithms_verify(self):
        for kind in ("ec", "rsa"):
            key, chain, fp = make_key(kind)
            text = manifest.sign(payload(), key, chain)
            self.assertEqual("0.9.9", manifest.releases(manifest.verify(text, [fp]))[0].version, kind)

    def test_fingerprint_may_be_pasted_with_separators(self):
        key, chain, fp = make_key("ec")
        pasted = ":".join(fp[i:i + 2] for i in range(0, 64, 2)).upper()
        self.assertTrue(manifest.verify(manifest.sign(payload(), key, chain), [pasted]))

    def test_unknown_signer_refused(self):
        key, chain, _fp = make_key("ec")
        _k, _c, other = make_key("ec")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(manifest.sign(payload(), key, chain), [other])
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(manifest.sign(payload(), key, chain), [])

    def test_tampered_payload_refused(self):
        key, chain, fp = make_key("rsa")
        text = resign(manifest.sign(payload(), key, chain), lambda b: b["releases"][0].update(version="9.9.9"))
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(text, [fp])

    def test_signer_claimed_by_a_forged_header_refused(self):
        """A certificate of the trusted fingerprint with someone else's signature."""
        key, chain, fp = make_key("ec")
        evil, _c, _f = make_key("ec")
        text = manifest.sign(payload(), evil, chain)
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(text, [fp])

    def test_other_algorithms_refused(self):
        key, chain, fp = make_key("ec")
        doc = json.loads(manifest.sign(payload(), key, chain))
        for alg in ("none", "HS256", "ES384"):
            header = json.loads(base64.urlsafe_b64decode(doc["signatures"][0]["protected"] + "=="))
            header["alg"] = alg
            forged = dict(doc, signatures=[dict(doc["signatures"][0], protected=base64.urlsafe_b64encode(
                json.dumps(header).encode()).rstrip(b"=").decode())])
            with self.assertRaises(manifest.ManifestError, msg=alg):
                manifest.verify(json.dumps(forged), [fp])
        with self.assertRaises(manifest.ManifestError):
            manifest.sign(payload(), key, chain, alg="none")

    def test_second_signature_is_enough(self):
        """The server adds its signature to the upstream's: either one may be the trusted one."""
        (k1, c1, f1), (k2, c2, f2) = make_key("rsa"), make_key("ec")
        first = json.loads(manifest.sign(payload(), k1, c1))
        second = json.loads(manifest.sign(payload(), k2, c2))
        both = dict(first, signatures=first["signatures"] + second["signatures"])
        # the second one signed its own copy of the payload: same bytes, the same issue time
        self.assertEqual(first["payload"], second["payload"])
        for fp in (f1, f2):
            self.assertTrue(manifest.verify(json.dumps(both), [fp]))

    def test_garbage_refused(self):
        for text in ("", "not json", "[]", "{}", '{"payload": 1, "signatures": []}',
                     '{"payload": "", "signatures": [{}]}'):
            with self.assertRaises(manifest.ManifestError, msg=text):
                manifest.verify(text, ["ab" * 32])

    def test_hostile_text_is_a_manifest_error_not_another_exception(self):
        key, chain, fp = make_key("ec")
        good = json.loads(manifest.sign(payload(), key, chain))
        # a trusted certificate copied into the header, with a payload that is not ASCII (it cannot be signed)
        for text in (json.dumps(dict(good, payload="é" + good["payload"])),
                     json.dumps(dict(good, signatures=[dict(good["signatures"][0], protected="é")])),
                     "[" * 100_000, '{"payload": "' + "a" * 10 + '", "signatures": ' + "[" * 100_000 + "]"):
            with self.assertRaises(manifest.ManifestError):
                manifest.verify(text, [fp])
        self.assertEqual([], manifest.signer_fingerprints("[" * 100_000))

    def test_times_without_an_offset_are_utc(self):
        p = payload(issued="2026-11-07")
        self.assertEqual("0.9.9", manifest.newer(p, "0.9.0", last_issued="2026-11-06T10:00:00Z").version)
        self.assertEqual("0.9.9", manifest.newer(payload(), "0.9.0", last_issued="2026-11-07T09:00:00").version)
        self.assertEqual("0.9.9", manifest.newer(p, "0.9.0", last_issued="2026-11-07T00:00:00+00:00").version)
        # the older one is still refused, whichever way each is written
        with self.assertRaises(manifest.ManifestError):
            manifest.newer(p, "0.9.0", last_issued="2026-11-07T10:00:00Z")
        with self.assertRaises(manifest.ManifestError):
            manifest.newer(payload(), "0.9.0", last_issued="2026-11-08")
        self.assertEqual("0.9.9", manifest.newer(payload(), "0.9.0", last_issued="not a time").version)

    def test_sizes_that_are_not_sizes_leave_the_package_out(self):
        for size in (-1, True, "3", 1.5, [3]):
            p = payload()
            p["releases"][0]["packages"][0]["size"] = size
            self.assertNotIn("0.9.9", [r.version for r in manifest.releases(p)], size)
        for releases in (5, "x", {"a": 1}, None):
            self.assertEqual([], manifest.releases({"releases": releases}))

    def test_channels_and_current_version(self):
        p = payload()
        self.assertEqual("0.9.9", manifest.newer(p, "0.9.8").version)
        self.assertIsNone(manifest.newer(p, "0.9.9"))
        beta = manifest.newer(p, "0.9.9", channels=("prod", "beta"))
        self.assertEqual(("1.0.0-beta1", True), (beta.version, beta.beta))
        self.assertIsNone(manifest.newer(p, "0.9.0", channels=("dev",)))

    def test_component_without_package_not_offered(self):
        self.assertIsNone(manifest.newer(payload(), "0.9.0", component="desktop-windows"))
        self.assertEqual("0.9.9", manifest.newer(payload(), "0.9.0", component="desktop-deb").version)

    def test_replay_of_an_older_manifest_refused(self):
        manifest.newer(payload(), "0.9.0", last_issued="2026-11-07T10:00:00Z")
        with self.assertRaises(manifest.ManifestError):
            manifest.newer(payload(), "0.9.0", last_issued="2026-11-08T00:00:00Z")
        with self.assertRaises(manifest.ManifestError):
            manifest.newer({"releases": []}, "0.9.0")

    def test_check_returns_release_and_issue_time(self):
        key, chain, fp = make_key("ec")
        release, issued = manifest.check(manifest.sign(payload(), key, chain), [fp], "0.9.8")
        self.assertEqual(("0.9.9", "2026-11-07T10:00:00Z"), (release.version, issued))

    def test_malformed_releases_left_out(self):
        p = payload()
        insecure = {"version": "2.0.0", "packages": [{"component": "a", "url": "http://x/", "sha256": SHA}]}
        p["releases"] += [{"version": "2.0.0"}, "x", insecure]
        self.assertEqual("0.9.9", manifest.newer(p, "0.9.8").version)
        self.assertEqual(["0.9.9", "1.0.0-beta1", "0.9.8"], [r.version for r in manifest.releases(p)])

    def test_package_checked_against_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.deb"
            f.write_bytes(b"abc")
            self.assertTrue(manifest.verify_file(f, hashlib.sha256(b"abc").hexdigest().upper()))
            self.assertFalse(manifest.verify_file(f, SHA))

    def test_download_keeps_only_a_matching_file(self):
        class Reply:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                data, self.data = self.data, b""
                return data

        good = manifest.Package("desktop-deb", "https://example.org/d.deb", hashlib.sha256(b"abc").hexdigest(), 3)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "d.deb"
            manifest.download(good, dest, opener=lambda req, timeout: Reply(b"abc"))
            self.assertEqual(b"abc", dest.read_bytes())
            dest.unlink()
            with self.assertRaises(manifest.ManifestError):
                manifest.download(good, dest, opener=lambda req, timeout: Reply(b"abd"))
            self.assertEqual([], list(Path(d).iterdir()))

    def test_download_is_bounded_by_the_declared_size(self):
        class Endless:
            reads = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                Endless.reads += 1
                return b"x" * 1024
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "d.deb"
            sha = hashlib.sha256(b"x" * 2048).hexdigest()
            # declared 2048: the stream is cut after it, the file dropped
            with self.assertRaises(manifest.ManifestError) as e:
                manifest.download(manifest.Package("a", "https://x/a", sha, 2048), dest,
                                  opener=lambda url, timeout: Endless())
            self.assertIn("larger", str(e.exception))
            self.assertEqual(3, Endless.reads)
            self.assertEqual([], list(Path(d).iterdir()))
            # shorter than declared: refused, even if the sha256 were the one of what came
            short = hashlib.sha256(b"abc").hexdigest()
            with self.assertRaises(manifest.ManifestError) as e:
                manifest.download(manifest.Package("a", "https://x/a", short, 10), dest,
                                  opener=lambda url, timeout: _Body(b"abc"))
            self.assertIn("size", str(e.exception))
            # no size declared (0): held to MAX_DOWNLOAD
            with mock.patch.object(manifest, "MAX_DOWNLOAD", 2000):
                Endless.reads = 0
                with self.assertRaises(manifest.ManifestError):
                    manifest.download(manifest.Package("a", "https://x/a", sha, 0), dest,
                                      opener=lambda url, timeout: Endless())
                self.assertEqual(2, Endless.reads)
                manifest.download(manifest.Package("a", "https://x/a", short, 0), dest,
                                  opener=lambda url, timeout: _Body(b"abc"))
                self.assertEqual(b"abc", dest.read_bytes())
                # a declared size beyond the cap is refused before anything is fetched
                with self.assertRaises(manifest.ManifestError):
                    manifest.download(manifest.Package("a", "https://x/a", sha, 3000), Path(d) / "big",
                                      opener=lambda url, timeout: self.fail("fetched"))

    def test_signer_fingerprints_shown_before_trust(self):
        key, chain, fp = make_key("ec")
        self.assertEqual([fp], manifest.signer_fingerprints(manifest.sign(payload(), key, chain)))
        self.assertEqual([], manifest.signer_fingerprints("nope"))


if __name__ == "__main__":
    unittest.main()
