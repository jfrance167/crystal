"""Exercise actual loopback HTTP and browser request boundaries."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from crystal.core import RotationEngine
from crystal.server import DemoServer


class HTTPTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / ".runtime" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.engine = RotationEngine(Path(self.temp.name) / "audit.sqlite3", enabled=False)
        self.server = DemoServer(0, self.engine)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        self.engine.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=4)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def authorized(self):
        return {"Origin": self.server.origin, "X-Crystal-Token": self.server.token,
                "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"}

    def test_dashboard_and_security_headers(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"A fresh key.", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn(self.server.token.encode(), body)
        for path in ["/style.css", "/app.js", "/crystal.svg"]:
            self.assertEqual(self.request("GET", path)[0], 200)

    def test_external_origin_host_and_fetch_site_are_rejected(self):
        for headers in [{"Origin": "https://evil.example"}, {"Host": "evil.example"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Host": "localhost:8765"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/session", headers=headers)[0], 403)

    def test_session_requires_custom_header_and_does_not_allow_cors(self):
        self.assertEqual(self.request("GET", "/api/session")[0], 403)
        status, headers, body = self.request("GET", "/api/session", headers={"X-Crystal-Client": "demo"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["token"], self.server.token)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotEqual(self.request("OPTIONS", "/api/rotate")[0], 200)

    def test_mutations_require_both_origin_and_session_token(self):
        for remove in ["Origin", "X-Crystal-Token"]:
            headers = self.authorized()
            del headers[remove]
            self.assertEqual(self.request("POST", "/api/rotate", "{}", headers)[0], 403)
        headers = self.authorized()
        headers["X-Crystal-Token"] = "incorrect"
        self.assertEqual(self.request("POST", "/api/rotate", "{}", headers)[0], 403)
        headers["X-Crystal-Token"] = "\u00e9"
        self.assertEqual(self.request("POST", "/api/rotate", "{}", headers)[0], 403)
        self.assertEqual(self.engine.status()["successful_rotations"], 0)

    def test_real_demo_endpoint_rejects_old_credential(self):
        old = self.engine.consumer._key
        self.assertEqual(self.request("GET", "/api/demo", headers={"Authorization": "Bearer " + old})[0], 200)
        self.assertEqual(self.request("POST", "/api/rotate", "{}", self.authorized())[0], 200)
        self.assertEqual(self.request("GET", "/api/demo", headers={"Authorization": "Bearer " + old})[0], 401)
        new = self.engine.consumer._key
        self.assertEqual(self.request("GET", "/api/demo", headers={"Authorization": "Bearer " + new})[0], 200)
        self.assertEqual(self.request("GET", "/api/demo")[0], 401)

    def test_failed_rotation_preserves_http_authentication(self):
        old = self.engine.consumer._key
        status, _, body = self.request("POST", "/api/rotate", '{"failure":"consumer_check"}', self.authorized())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["outcome"], "failed")
        self.assertEqual(self.request("GET", "/api/demo", headers={"Authorization": "Bearer " + old})[0], 200)

    def test_idempotent_post_only_rotates_once(self):
        headers = self.authorized()
        self.request("POST", "/api/rotate", "{}", headers)
        status, _, body = self.request("POST", "/api/rotate", "{}", headers)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["duplicate"])
        self.assertEqual(self.engine.status()["successful_rotations"], 1)

    def test_bounded_body_and_strict_fields(self):
        for body in ['{', '[]', '{"secret":"do-not-reflect"}', '{"failure":[]}', '{"failure":"unknown"}']:
            with self.subTest(body=body):
                status, _, response = self.request("POST", "/api/rotate", body, self.authorized())
                self.assertEqual(status, 400)
                self.assertNotIn(b"do-not-reflect", response)
        self.assertEqual(self.request("POST", "/api/rotate", "x" * 1025, self.authorized())[0], 413)
        headers = self.authorized()
        headers["Content-Type"] = "text/plain"
        self.assertEqual(self.request("POST", "/api/rotate", "{}", headers)[0], 415)
        headers = self.authorized()
        headers["Transfer-Encoding"] = "chunked"
        self.assertEqual(self.request("POST", "/api/rotate", "{}", headers)[0], 400)
        headers = self.authorized()
        headers["Content-Length"] = "9" * 5000
        self.assertEqual(self.request("POST", "/api/rotate", "{}", headers)[0], 413)

    def test_schedule_input_validation(self):
        for body in [{"interval": 10, "enabled": "yes"}, {"interval": True, "enabled": True},
                     {"interval": 9, "enabled": True}, {"interval": 86401, "enabled": True},
                     {"interval": 10, "enabled": False, "extra": 1}]:
            self.assertEqual(self.request("POST", "/api/schedule", json.dumps(body), self.authorized())[0], 400)
        self.assertEqual(self.request("POST", "/api/schedule", '{"interval":60,"enabled":true}', self.authorized())[0], 200)
        self.assertTrue(self.engine.status()["schedule"]["enabled"])

    def test_unrecognized_paths_cannot_read_files(self):
        for path in ["/../core.py", "/%2e%2e/core.py", "/api/status?token=arbitrary"]:
            self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
