# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import datetime
import hashlib
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from activitytrack import __version__, wire
from activitytrack.net import Http, NetworkError, PinMismatch, key_pin, local_host


def self_signed(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "at.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = Path(folder) / "cert.pem", Path(folder) / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert_file, key_file, base64.b64encode(hashlib.sha256(spki).digest()).decode()


class Handler(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        Handler.seen.append((self.path, dict(self.headers)))
        body = b'{"rev": 7}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # the refused handshakes of the tests
        pass


class PinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cert, key, cls.pin = self_signed(cls.dir.name)
        cls.httpd = Server(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        cls.httpd.socket = ctx.wrap_socket(cls.httpd.socket, server_side=True)
        cls.url = f"https://127.0.0.1:{cls.httpd.server_address[1]}/at"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.dir.cleanup()

    def test_the_pinned_key_is_trusted_whatever_the_name(self):
        reply = Http(self.url, self.pin, token="tok").send("GET", "sync/head")
        self.assertEqual((reply.status, reply.body), (200, '{"rev": 7}'))
        path, headers = Handler.seen[-1]
        self.assertEqual(path, "/at/api/v1/sync/head")
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(headers[wire.APP_HEADER], __version__)
        self.assertEqual(headers[wire.SCHEMA_HEADER], str(wire.SCHEMA))

    def test_an_answer_longer_than_expected_is_refused(self):
        http = Http(self.url, self.pin, token="tok")
        # the answer of the test server is 10 bytes
        self.assertEqual((200, b'{"rev": 7}'), http.exchange("GET", "sync/head", max_bytes=10))
        with self.assertRaises(NetworkError) as e:
            http.exchange("GET", "sync/head", max_bytes=9)
        self.assertIn("longer", str(e.exception))

    def test_another_key_is_refused_before_sending(self):
        count = len(Handler.seen)
        other = base64.b64encode(bytes(32)).decode()
        with self.assertRaises(PinMismatch):
            Http(self.url, other, token="tok").send("GET", "sync/head")
        self.assertEqual(len(Handler.seen), count)

    def test_without_a_pin_the_self_signed_certificate_is_not_trusted(self):
        with self.assertRaises(NetworkError) as e:
            Http(self.url).send("GET", "sync/head")
        self.assertTrue(e.exception.certificate)

    def test_http_only_when_allowed(self):
        with self.assertRaises(NetworkError):
            Http("http://127.0.0.1:1")

    def test_with_dev_http_only_to_a_local_server(self):
        for server in ("http://127.0.0.1:8000", "http://localhost", "http://[::1]:8000", "http://10.1.2.3",
                       "http://172.20.0.1", "http://192.168.1.10/at", "http://[fd00::1]", "http://169.254.1.1"):
            Http(server, allow_http=True)
        for server in ("http://8.8.8.8", "http://at.example.com", "http://172.32.0.1", "http://[2001:db8::1]",
                       "http://localhost.example.com"):
            with self.assertRaises(NetworkError):
                Http(server, allow_http=True)
        # https is never restricted
        Http("https://at.example.com", allow_http=True)

    def test_local_hosts(self):
        self.assertTrue(local_host("LOCALHOST."))
        self.assertTrue(local_host("fe80::1%eth0"))
        self.assertTrue(local_host("::ffff:192.168.0.1"))
        self.assertFalse(local_host("printer.local"))
        self.assertFalse(local_host(None))

    def test_a_certificate_that_cannot_be_read_is_a_certificate_error(self):
        with self.assertRaises(NetworkError) as e:
            key_pin(b"not a certificate")
        self.assertTrue(e.exception.certificate)
