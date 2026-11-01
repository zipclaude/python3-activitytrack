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
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import client, wire

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
    key = x509.load_der_x509_certificate(der_certificate).public_key()
    spki = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()


class Http:
    def __init__(self, server, pin=None, token=None, allow_http=False):
        u = urlsplit(server)
        if u.scheme not in ("https", "http") or (u.scheme == "http" and not allow_http):
            raise NetworkError("not an https address")
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
            if isinstance(e, NetworkError):
                raise
            raise NetworkError(str(e) or type(e).__name__) from e
        finally:
            c.close()

    def send(self, method, path, body=None):
        """JSON [body] (text) to [path]; the answer as text."""
        data = body.encode() if body is not None else None
        status, raw = self.exchange(method, path, data, "application/json" if data is not None else None)
        return Reply(status, raw.decode("utf-8", "replace"), raw)
