# SPDX-License-Identifier: GPL-3.0-or-later
"""
The connection to ActivityTrack Web (app sync/SyncHttp.kt): JSON over
HTTPS under api/v1/. With a pin (self-signed certificate, SYNC.md §9) the
server is trusted by its public key alone, whatever its certificate or host
name; without one, the system's trust store and host name check apply.
"""

import base64
import hashlib
import http.client
import ipaddress
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import client, wire
from .i18n import _

CONNECT_TIMEOUT, READ_TIMEOUT = 15, 60


class NetworkError(Exception):
    def __init__(self, message, certificate=False):
        super().__init__(message)
        # the server's certificate is not trusted (no pin, or not valid)
        self.certificate = certificate


class PinMismatch(NetworkError):
    def __init__(self):
        super().__init__("server key does not match the pairing code", certificate=True)


@dataclass(frozen=True)
class Reply:
    status: int
    body: str
    data: bytes = b""


def key_pin(der_certificate):
    """base64 SHA-256 of the certificate's SubjectPublicKeyInfo, as `openssl pkey -pubin -outform der`."""
    try:
        key = x509.load_der_x509_certificate(der_certificate).public_key()
    except ValueError as e:
        # a certificate OpenSSL took and cryptography cannot read: not one to trust
        raise NetworkError(f"unreadable server certificate: {e}", certificate=True) from e
    spki = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()


# where --dev lets http:// go: loopback, private and link-local networks
_LOCAL_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10")]


def local_host(host):
    """
    [host] is this PC or on a private network: a literal address of those
    networks, or localhost. Names are not resolved: any other name could
    point anywhere, so a development server on the LAN is given by address.
    """
    host = (host or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in n for n in _LOCAL_NETWORKS)


def http_refused(server, allow_http=False):
    """Why [server] cannot be used, or None: https, or http:// with --dev to a local server."""
    u = urlsplit(server)
    if u.scheme == "https" or (u.scheme == "http" and allow_http and local_host(u.hostname)):
        return None
    if u.scheme == "http" and allow_http:
        return _("http:// è permesso solo per un server locale (localhost o una rete privata).")
    return _("Il server deve usare HTTPS.")


class Http:
    def __init__(self, server, pin=None, token=None, allow_http=False):
        refused = http_refused(server, allow_http)
        if refused:
            raise NetworkError(refused)
        u = urlsplit(server)
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.base = u.path.rstrip("/") + "/api/v1/"
        self.pin = pin
        self.token = token

    def _connection(self):
        if self.scheme == "http":
            return http.client.HTTPConnection(self.host, self.port, timeout=CONNECT_TIMEOUT)
        if self.pin:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            # already Python's default; said here, as this context checks nothing else
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        else:
            ctx = ssl.create_default_context()
        return http.client.HTTPSConnection(self.host, self.port, timeout=CONNECT_TIMEOUT, context=ctx)

    def exchange(self, method, path, body=None, content_type=None, headers=None):
        """One request; the pin is checked before anything is sent."""
        c = self._connection()
        try:
            c.connect()
            if self.pin and key_pin(c.sock.getpeercert(binary_form=True)) != self.pin:
                raise PinMismatch()
            c.sock.settimeout(READ_TIMEOUT)
            me = client()
            h = {
                "User-Agent": f"{me['name']}/{me['version']}",
                wire.APP_HEADER: me["version"],
                wire.SCHEMA_HEADER: str(wire.SCHEMA),
                "Accept": "application/json",
            }
            if self.token:
                h["Authorization"] = f"Bearer {self.token}"
            if body is not None:
                h["Content-Type"] = content_type or "application/octet-stream"
            h.update(headers or {})
            c.request(method, self.base + path, body=body, headers=h)
            r = c.getresponse()
            return r.status, r.read()
        except ssl.SSLCertVerificationError as e:
            raise NetworkError(str(e), certificate=True) from e
        except (OSError, http.client.HTTPException) as e:
            raise NetworkError(str(e) or type(e).__name__) from e
        finally:
            c.close()

    def send(self, method, path, body=None):
        """JSON [body] (text) to [path]; the answer as text."""
        data = body.encode() if body is not None else None
        status, raw = self.exchange(method, path, data, "application/json" if data is not None else None)
        return Reply(status, raw.decode("utf-8", "replace"), raw)
