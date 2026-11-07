# SPDX-License-Identifier: GPL-3.0-or-later
"""
The signed update manifest (activitytrack#1, docs/UPDATES.md): one JSON file
with the signature inline, a JWS in JSON serialization (RFC 7515):

    {"payload": <base64url of the manifest>,
     "signatures": [{"protected": <base64url of {"alg", "x5c", "x5t#S256"}>,
                     "signature": <base64url>}, ...]}

Only ES256 and RS256 count: "none", HS* and anything else are refused. The
signer's certificate travels in the protected header (x5c, standard base64
DER, leaf first); a signature is accepted when its leaf's SHA-256
fingerprint is one the caller trusts, so on the PC the pin of the paired
server's key is the only anchor: no upstream and no custom address.

The manifest lists releases by channel (prod, beta, test, dev, ...), each
with its packages: component, address, sha256, size. A package is checked
against its sha256 before the interface hands it to the installer.
"""

import base64
import hashlib
import json
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from .versions import compare

FORMAT = "activitytrack-update-manifest"
ALGORITHMS = ("ES256", "RS256")
MAX_BYTES = 1024 * 1024
# the packages the server itself keeps (docs/SYNC.md §12.4): only a manifest the server relayed may name them
SERVER_FILES = "/api/v1/update/files/"


class ManifestError(Exception):
    """Not a manifest, not signed by anyone trusted, or a package that does not match."""


@dataclass(frozen=True)
class Package:
    component: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Release:
    version: str
    channel: str
    published: str
    notes_url: str
    packages: tuple

    @property
    def beta(self):
        return self.channel != "prod"

    def package(self, component):
        return next((p for p in self.packages if p.component == component), None)


def _b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64u(text):
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as e:
        raise ManifestError("bad base64") from e


def fingerprint(cert_der):
    """The SHA-256 fingerprint of a certificate (DER), lowercase hex without separators."""
    return hashlib.sha256(cert_der).hexdigest()


def normalize(fp):
    """What an administrator pastes ("AB:CD ...", spaces, upper case) as the comparable form."""
    return "".join(c for c in fp.lower() if c in "0123456789abcdef")


def _check_signature(cert, alg, signing_input, signature):
    key = cert.public_key()
    try:
        if alg == "ES256":
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1" or len(signature) != 64:
                return False
            r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            der = utils.encode_dss_signature(r, s)
            key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        else:
            if not isinstance(key, rsa.RSAPublicKey):
                return False
            key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False
    return True


def verify(text, trusted):
    """The manifest's payload (a dict) when one of its signatures is valid and its signer's leaf
    certificate has a fingerprint in [trusted]; ManifestError otherwise."""
    pins = {normalize(f) for f in trusted}
    if not pins:
        raise ManifestError("no trusted signer")
    if len(text) > MAX_BYTES:
        raise ManifestError("too large")
    try:
        doc = json.loads(text)
        payload_b64 = doc["payload"]
        signatures = doc["signatures"]
        if not isinstance(payload_b64, str) or not isinstance(signatures, list):
            raise TypeError
    except (ValueError, KeyError, TypeError) as e:
        raise ManifestError("not a signed manifest") from e
    for s in signatures:
        try:
            protected_b64 = s["protected"]
            header = json.loads(_unb64u(protected_b64))
            alg = header["alg"]
            leaf = base64.b64decode(header["x5c"][0], validate=True)
            signature = _unb64u(s["signature"])
        except (ValueError, KeyError, TypeError, IndexError, ManifestError):
            continue
        if alg not in ALGORITHMS or fingerprint(leaf) not in pins:
            continue
        try:
            cert = x509.load_der_x509_certificate(leaf)
        except ValueError:
            continue
        if _check_signature(cert, alg, f"{protected_b64}.{payload_b64}".encode("ascii"), signature):
            try:
                payload = json.loads(_unb64u(payload_b64))
            except ValueError as e:
                raise ManifestError("bad payload") from e
            if not isinstance(payload, dict) or payload.get("format") != FORMAT:
                raise ManifestError("not an update manifest")
            return payload
    raise ManifestError("no valid signature of a trusted signer")


def signer_fingerprints(text):
    """The leaf fingerprints the manifest's signatures claim (not verified): what a first pairing shows."""
    found = []
    try:
        for s in json.loads(text)["signatures"]:
            leaf = base64.b64decode(json.loads(_unb64u(s["protected"]))["x5c"][0], validate=True)
            found.append(fingerprint(leaf))
    except (ValueError, KeyError, TypeError, IndexError, ManifestError):
        pass
    return found


def _when(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _address_ok(url, server):
    url = str(url)
    if url.startswith("https://"):
        return True
    return server and url.startswith(SERVER_FILES) and url[len(SERVER_FILES):].isalnum()


def releases(payload, server=False):
    """The well-formed releases of a verified payload; anything malformed is left out. [server]:
    the manifest is the paired server's, so a package may be one of the server's own files."""
    out = []
    for o in payload.get("releases") or ():
        try:
            packages = tuple(
                Package(p["component"], p["url"], normalize(p["sha256"]), int(p.get("size") or 0))
                for p in o["packages"]
                if _address_ok(p["url"], server) and len(normalize(p["sha256"])) == 64)
            if not packages:
                continue
            out.append(Release(str(o["version"]), str(o.get("channel") or "prod"), str(o.get("published") or ""),
                               str(o.get("notes_url") or ""), packages))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def newer(payload, current, channels=("prod",), component=None, last_issued=None, server=False):
    """The newest release after [current] in one of [channels] (with a package of [component],
    when given), or None. A manifest issued before [last_issued] (an ISO time, the one accepted
    last) is a replay of an older file and is refused."""
    issued = _when(payload.get("issued"))
    if issued is None:
        raise ManifestError("no issue time")
    previous = _when(last_issued) if last_issued else None
    if previous is not None and issued < previous:
        raise ManifestError("older than the last manifest")
    best = None
    for r in releases(payload, server):
        if r.channel not in channels or compare(r.version, current) <= 0:
            continue
        if component is not None and r.package(component) is None:
            continue
        if best is None or compare(r.version, best.version) > 0:
            best = r
    return best


def check(text, trusted, current, channels=("prod",), component=None, last_issued=None, server=False):
    """Verify [text] and pick the release to offer: (release or None, the manifest's issue time)."""
    payload = verify(text, trusted)
    return newer(payload, current, channels, component, last_issued, server), payload["issued"]


def verify_file(path, sha256):
    """True when the file's SHA-256 is [sha256]."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == normalize(sha256)


def download(package, dest, opener=None, timeout=60):
    """Fetch [package] to the file [dest]; the file stays only if its sha256 matches (else
    ManifestError and nothing is left). [opener](address, timeout): the paired server's files
    (relative address) go through its authorisation, so they need one."""
    from . import client
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    if opener is None:
        if not package.url.startswith("https://"):
            raise ManifestError("a file of the server needs the server's authorisation")
        me = client()
        web = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))

        def opener(url, timeout):
            return web.open(urllib.request.Request(url, headers={"User-Agent": f"{me['name']}/{me['version']}"}),
                            timeout=timeout)
    h = hashlib.sha256()
    try:
        with opener(package.url, timeout) as r, open(part, "wb") as f:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != package.sha256:
            raise ManifestError("sha256 does not match")
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def from_package_manager():
    """True when this library was installed by the distribution (under /usr, not pip or the
    Windows installer): the package manager updates it, so no download is offered."""
    here = Path(__file__).resolve()
    return here.as_posix().startswith(("/usr/lib/", "/usr/share/", "/usr/local/lib/python3/dist-packages/"))


def sign(payload, key_pem, chain_pem, alg=None):
    """A signed manifest of [payload] (a dict): the key and chain in PEM (leaf first). The
    release's CI and the tests use it; the server has its own signer in the same format."""
    key = serialization.load_pem_private_key(key_pem, password=None)
    certs = x509.load_pem_x509_certificates(chain_pem)
    der = [c.public_bytes(serialization.Encoding.DER) for c in certs]
    if alg is None:
        alg = "ES256" if isinstance(key, ec.EllipticCurvePrivateKey) else "RS256"
    if alg not in ALGORITHMS:
        raise ManifestError(f"algorithm {alg}")
    header = {"alg": alg, "x5c": [base64.b64encode(d).decode("ascii") for d in der],
              "x5t#S256": _b64u(hashlib.sha256(der[0]).digest())}
    protected = _b64u(json.dumps(header, separators=(",", ":")).encode())
    body = _b64u(json.dumps({"format": FORMAT, "v": 1, **payload}, separators=(",", ":"), ensure_ascii=False).encode())
    data = f"{protected}.{body}".encode("ascii")
    if alg == "ES256":
        r, s = utils.decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:
        signature = key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    return json.dumps({"payload": body, "signatures": [{"protected": protected, "signature": _b64u(signature)}]})
