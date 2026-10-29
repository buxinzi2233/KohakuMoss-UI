"""Loopback HTTP security and persistence tests; no generator or GPU required."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.server.http import make_server


class GbcApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(self.base / "state")})
        self.env.start()
        self.server = make_server(TrainerRoot(self.base), "127.0.0.1", 0, "gbc-secret")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.env.stop()
        self.tmp.cleanup()

    def call(self, route="capabilities", method="GET", body=None, auth=True, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        hdr = {"X-UI-Token": "gbc-secret"} if auth else {}
        if body is not None:
            hdr["Content-Type"] = "application/json"
        hdr.update(headers or {})
        con.request(method, "/api/gbc/v1/" + route, body=body, headers=hdr)
        response = con.getresponse()
        result = response.status, json.loads(response.read())
        con.close()
        return result

    def test_all_gets_authenticated_and_versioned(self):
        status, payload = self.call(auth=False)
        self.assertEqual(status, 403)
        self.assertEqual(payload["apiVersion"], "gbc-ui-v1")
        self.assertEqual(payload["error"]["code"], "AUTH_REQUIRED")
        self.assertEqual(self.call("capabilities?token=gbc-secret", auth=False)[0], 403)
        self.assertEqual(self.call(auth=False, headers={"Cookie": "km_token=gbc-secret"})[0], 403)
        status, payload = self.call()
        self.assertEqual(status, 200)
        self.assertFalse(payload["data"]["features"]["remoteApi"])

    def test_origin_host_and_methods(self):
        self.assertEqual(self.call(headers={"Origin": "https://evil.invalid"})[0], 403)
        self.assertEqual(self.call(headers={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.call(method="PUT", body="{}")[0], 405)
        self.assertEqual(self.call("settings?x=1&x=2")[0], 400)

    def test_strict_json_and_oversize(self):
        for raw in ['{"a":1,"a":2}', '{"a":NaN}', '[]', '{"a":"\\ud800"}']:
            status, payload = self.call("settings", "POST", raw)
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["code"], "INVALID_JSON")
        self.assertEqual(self.call("settings", "POST", "{}", headers={"Content-Length": "1000001"})[0], 413)

    def test_duplicate_framing_headers(self):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        con.putrequest("POST", "/api/gbc/v1/settings")
        con.putheader("X-UI-Token", "gbc-secret")
        con.putheader("Content-Type", "application/json")
        con.putheader("Content-Length", "2")
        con.putheader("Content-Length", "2")
        con.endheaders(b"{}")
        reply = con.getresponse()
        self.assertEqual(reply.status, 400)
        self.assertEqual(json.loads(reply.read())["error"]["code"], "INVALID_REQUEST")
        con.close()
        self.assertEqual(self.call("jobs/logs?cursor=not-a-number")[0], 400)

    def test_cas_idempotency_unknown_and_injection(self):
        value = {"apiVersion": "gbc-ui-v1", "requestId": "same_request", "expectedRevision": 0}
        first = self.call("settings", "POST", json.dumps(value))
        self.assertEqual(first[0], 200)
        self.assertEqual(first, self.call("settings", "POST", json.dumps(value)))
        value["expectedRevision"] = 1
        self.assertEqual(self.call("settings", "POST", json.dumps(value))[0], 409)
        value.update(requestId="new_request", expectedRevision=0)
        result = self.call("settings", "POST", json.dumps(value))
        self.assertEqual(result[1]["error"]["code"], "VERSION_CONFLICT")
        value.update(requestId="unknown", expectedRevision=1, source="__import__('os').system('echo unsafe')")
        self.assertEqual(self.call("settings", "POST", json.dumps(value))[0], 400)
        self.assertEqual(self.call("requests?requestId=same_request")[1]["data"]["request"]["receipt"], first[1])

    def test_explicit_root_registration_rejects_symlink(self):
        real = self.base / "input"
        real.mkdir()
        link = self.base / "link"
        link.symlink_to(real)
        value = {"apiVersion": "gbc-ui-v1", "requestId": "linked", "expectedRevision": 0,
                 "roots": [{"path": str(link), "purpose": "input", "confirmTrust": True}]}
        result = self.call("settings", "POST", json.dumps(value))
        self.assertEqual(result[1]["error"]["code"], "UNSAFE_PATH")
        value["roots"][0]["path"] = str(real)
        value["requestId"] = "safe"
        self.assertEqual(self.call("settings", "POST", json.dumps(value))[0], 200)


if __name__ == "__main__":
    unittest.main()
