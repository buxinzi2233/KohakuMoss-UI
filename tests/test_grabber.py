"""Offline ASG contract tests, including real loopback HTTP smoke coverage."""

import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from kohakumoss_ui.contract import grabber
from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.server.http import make_server
from kohakumoss_ui.store import jsonfile


class FakeASG(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.seen.append((self.path, None))
        payload = {"status": "running", "logs": ["api_key=very-private"],
                   "items": [], "log_count": 7, "item_count": 9}
        if self.path == "/api/sources":
            payload = {"sources": [{"id": s} for s in sorted(grabber.SOURCES)]}
        if self.server.reply is not None:
            payload = self.server.reply
        self.respond(payload)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.seen.append((self.path, body))
        payload = self.server.reply if self.server.reply is not None else {"ok": True}
        self.respond(payload)

    def respond(self, payload):
        self.send_response(self.server.code)
        if self.server.code == 302:
            self.send_header("Location", "http://example.invalid/secret")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


class GrabberCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.asg = self.base / "ArtistSourceGrabber"
        self.asg.mkdir()
        (self.asg / "app.py").write_text("# fake app\n")
        (self.asg / "downloads").mkdir()
        self.env = mock.patch.dict(os.environ, {
            "KOHAKUMOSS_UI_STATE": str(self.base / "state"),
            "KOHAKUMOSS_GRABBER_ROOT": str(self.asg),
            "KOHAKUMOSS_GRABBER_PYTHON": "",
        })
        self.env.start()
        self.root = TrainerRoot(self.base / "trainer")
        self.client = grabber.Grabber(self.root)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.client.close)

    def upstream(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeASG)
        server.code, server.reply, server.seen = 200, None, []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(close)
        self.client._process = mock.Mock(pid=123)
        self.client._process.poll.return_value = None
        self.client._port = server.server_address[1]
        return server


class CredentialTests(GrabberCase):
    def test_save_read_blank_clear_private(self):
        saved = grabber.credentials("danbooru", {"login": "moss", "api_key": "very-private"})
        self.assertEqual(saved, {"source": "danbooru", "values": {"login": "moss"}, "configured": ["api_key"]})
        grabber.credentials("danbooru", {"api_key": ""})
        self.assertEqual(self.client._merge({"source": "danbooru"})["api_key"], "very-private")
        self.assertEqual((self.base / "state/grabber-credentials.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(grabber.credentials("danbooru", {}, ["api_key"])["configured"], [])
        self.assertNotIn("api_key", self.client._merge({"source": "danbooru"}))

    def test_whitelists_and_embedded_url_credentials(self):
        for source, values in [("e621", {}), ("danbooru", {"unknown": "x"}),
                               ("twitter", {"api_key": "x"}),
                               ("danbooru", {"api_key": {"nested": "x"}}),
                               ("danbooru", {"proxy": "http://user:password@host"})]:
            with self.subTest(source=source, values=values), self.assertRaises(ValueError):
                grabber.credentials(source, values)
        for source in grabber.FIELDS:
            self.assertEqual(grabber.credentials(source)["source"], source)

    def test_source_and_tagger_secrets_are_separate(self):
        grabber.credentials("danbooru", {"api_key": "source-private", "login": "saved"})
        grabber.credentials("tagger:openai_compatible", {"api_key": "tag-private", "endpoint": "http://localhost:8000/v1", "model": "test"})
        body = {"source": "danbooru", "login": "override", "tagger_id": "openai_compatible", "tagger_config": {"api_key": "", "temperature": 0}}
        merged = self.client._merge(body)
        self.assertEqual(merged["api_key"], "source-private")
        self.assertEqual(merged["tagger_config"]["api_key"], "tag-private")
        self.assertEqual(merged["tagger_config"]["temperature"], 0)
        self.assertEqual(merged["login"], "override")
        self.assertNotIn("api_key", body)
        self.assertNotIn("source-private", json.dumps(grabber.credentials("danbooru")))
        with self.assertRaises(ValueError):
            self.client._merge({"source": "twitter", "api_key": "wrong"})
        with self.assertRaises(ValueError):
            self.client._merge({"source": "e621"}, source_required=False)
        with self.assertRaises(ValueError):
            self.client._merge({"sources": ["danbooru", "pixiv"]}, allow_multi=False)

    def test_corrupt_credentials_backup_is_private(self):
        path = self.base / "state/grabber-credentials.json"
        path.parent.mkdir()
        path.write_text('{"api_key":"private", broken')
        grabber.credentials("danbooru")
        self.assertEqual(path.with_name(path.name + ".bak").stat().st_mode & 0o777, 0o600)

    def test_concurrent_saves_do_not_lose_other_sources(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda s: grabber.credentials(s, {"proxy": "http://127.0.0.1:9999"}), grabber.SOURCES))
        records = jsonfile.read_json(self.base / "state/grabber-credentials.json", {})
        self.assertEqual(set(records), grabber.SOURCES)


class ProxyTests(GrabberCase):
    def test_unstarted_status_and_get_do_not_spawn_or_adopt(self):
        with mock.patch.object(grabber.subprocess, "Popen") as popen:
            self.assertFalse(self.client.status()["running"])
            with self.assertRaises(grabber.GrabberError) as error:
                self.client.get("sources", {})
            self.assertEqual(error.exception.status, 503)
            popen.assert_not_called()
        (self.asg / "app.py").unlink()
        self.assertFalse(self.client.status()["available"])
        self.assertFalse(self.client.start_service()["running"])

    def test_progress_offsets_sources_stop_and_session_mapping(self):
        server = self.upstream()
        response = self.client.get("progress", {"logs": ["4"], "items": ["5"]})
        self.assertEqual((response["log_count"], response["item_count"]), (7, 9))
        self.assertEqual(server.seen[-1][0], "/api/progress?logs=4&items=5")
        self.assertNotIn("very-private", json.dumps(response))
        self.assertEqual(len(self.client.get("sources", {})["sources"]), 8)
        self.assertTrue(self.client.post("task/stop", {"force": True})["ok"])
        self.assertEqual(server.seen[-1], ("/api/stop", {}))
        self.assertEqual(self.client.get("progress", {})["status"], "running")
        for source, prefix in [("twitter", "x"), ("pixiv", "pixiv")]:
            self.client.post("session/check", {"source": source})
            self.assertEqual(server.seen[-1][0], "/api/" + prefix + "/session/check")
        for offsets in [{"logs": ["-1"]}, {"items": ["oops"]}]:
            with self.assertRaises(ValueError):
                self.client.get("progress", offsets)

    def test_http200_failure_and_error_redaction(self):
        server = self.upstream()
        server.reply = {"ok": False, "error": "Failed credential very-private", "api_key": "very-private", "config": {"login": "x"}}
        result = self.client.post("source/test", {"source": "danbooru", "api_key": "very-private"})
        self.assertFalse(result["ok"])
        self.assertNotIn("very-private", json.dumps(result))
        self.assertNotIn("config", result)
        for code in (400, 401, 403, 409, 500):
            server.code = code
            with self.assertRaises(grabber.GrabberError) as error:
                self.client.post("artists/search", {"source": "danbooru", "query": "test"})
            self.assertEqual(error.exception.status, 502 if code == 500 else code)
            self.assertNotIn("very-private", json.dumps(error.exception.payload))

    def test_redirect_is_not_followed_and_proxy_is_disabled(self):
        self.assertFalse(any(isinstance(h, ProxyHandler) and h.proxies for h in self.client._opener.handlers))
        server = self.upstream()
        server.code = 302
        with self.assertRaises(grabber.GrabberError) as error:
            self.client.get("sources", {})
        self.assertEqual(error.exception.status, 502)
        self.assertEqual(len(server.seen), 1)

    def test_start_lock_rejects_concurrent_submission(self):
        self.client._task_lock.acquire()
        try:
            with self.assertRaises(grabber.GrabberError) as error:
                self.client.post("task/start", {"source": "danbooru"})
            self.assertEqual(error.exception.status, 409)
        finally:
            self.client._task_lock.release()

    def test_start_merge_and_tagger_test(self):
        server = self.upstream()
        grabber.credentials("tagger:local_onnx", {"model_path": "/tmp/test.onnx", "tags_path": "/tmp/test.csv"})
        self.client.post("tagger/test", {"tagger_id": "local_onnx", "tagger_config": {}})
        self.assertEqual(server.seen[-1][1]["tagger_config"]["model_path"], "/tmp/test.onnx")
        grabber.credentials("pixiv", {"access_token": "pixiv-secret"})
        self.client.post("task/start", {"source": "pixiv", "artist": "123"})
        self.assertEqual(server.seen[-1][0], "/api/start")
        self.assertEqual(server.seen[-1][1]["access_token"], "pixiv-secret")


class ServiceTests(GrabberCase):
    def test_launch_failure_is_safe(self):
        with mock.patch.object(grabber.subprocess, "Popen", side_effect=OSError("private internal data")):
            state = self.client.start_service()
        self.assertFalse(state["running"])
        self.assertNotIn("private internal data", state["error"])
        self.assertIsNone(self.client._stdout)

    def test_start_timeout_cleans_only_owned_child(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with mock.patch.object(grabber.subprocess, "Popen", return_value=process), mock.patch.object(grabber, "START_TIMEOUT", 0):
            state = self.client.start_service()
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        process.kill.assert_not_called()
        self.assertFalse(state["running"])
        self.assertIsNone(self.client._port)

    def test_real_banner_required_and_readiness_confirmed(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        def launch(*args, **kwargs):
            self.assertIn("--no-browser", args[0])
            self.assertEqual(kwargs["env"]["PYTHONUNBUFFERED"], "1")
            self.assertTrue(kwargs["start_new_session"])
            kwargs["stdout"].write("  \u754c\u9762\u5730\u5740\uff1ahttp://127.0.0.1:8714\n".encode())
            kwargs["stdout"].flush()
            return process
        with mock.patch.object(grabber.subprocess, "Popen", side_effect=launch) as popen, mock.patch.object(self.client, "_request", return_value={"status": "idle"}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                states = list(pool.map(lambda _: self.client.start_service(), range(2)))
            self.assertTrue(all(s["running"] for s in states))
            self.assertEqual(states[0]["port"], 8714)
            popen.assert_called_once()
        self.client.close()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertIsNone(self.client._stdout)

    def test_random_url_does_not_select_port(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        def launch(*args, **kwargs):
            kwargs["stdout"].write(b"dependency error http://127.0.0.1:8710\n")
            kwargs["stdout"].flush()
            return process
        with mock.patch.object(grabber.subprocess, "Popen", side_effect=launch), mock.patch.object(grabber, "START_TIMEOUT", 0.05), mock.patch.object(self.client, "_request") as request:
            self.assertFalse(self.client.start_service()["running"])
            request.assert_not_called()


class FileTests(GrabberCase):
    def test_raster_and_caption_only_and_traversal(self):
        root = self.asg / "downloads"
        (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nexample")
        (root / "tags.txt").write_text("tag, artist")
        (root / "evil.png").write_text("<svg onload='alert(1)'></svg>")
        (root / "evil.svg").write_text("<svg/>")
        (self.base / "secret.txt").write_text("do not expose")
        (root / "link.txt").symlink_to(self.base / "secret.txt")
        (root / "inside.txt").symlink_to(root / "tags.txt")
        self.assertEqual(self.client.file("/files/image.png")[1], "image/png")
        self.assertEqual(self.client.file("/files/tags.txt")[0], b"tag, artist")
        for raw in ["/files/../secret.txt", "/files/%2e%2e/secret.txt", "/files/%252e%252e/secret.txt", "/files//secret.txt", "/files/link.txt", "/files/inside.txt", "/files/evil.png", "/files/evil.svg", "http://localhost/files/tags.txt", "/files/a\\b.txt", "/files/%00.txt"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.client.file(raw)
        os.mkfifo(root / "pipe.txt")
        with self.assertRaises(ValueError):
            self.client.file("/files/pipe.txt")


class HttpSmokeTests(GrabberCase):
    def test_actual_ui_http_token_status_credentials_progress_and_file(self):
        upstream = self.upstream()
        server = make_server(self.root, "127.0.0.1", 0, "test-token")
        server.RequestHandlerClass.api.grabber = self.client
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = build_opener(ProxyHandler({}))
        def call(route, body=None, token="test-token"):
            request = Request(f"http://127.0.0.1:{server.server_address[1]}/api/grabber/{route}",
                              data=None if body is None else json.dumps(body).encode(),
                              headers={"X-UI-Token": token, "Content-Type": "application/json"})
            try:
                response = opener.open(request, timeout=3)
            except HTTPError as error:
                response = error
            with response:
                return response.status, response.read(), response.headers
        for route, body in [("status", None), ("credentials?source=twitter", None), ("service/start", {}), ("task/start", {})]:
            self.assertEqual(call(route, body, token="")[0], 403)
            self.assertEqual(call(route, body, token="wrong")[0], 403)
        self.assertTrue(json.loads(call("status")[1])["running"])
        status, data, _ = call("credentials", {"source": "twitter", "values": {"auth_token": "private-cookie"}})
        self.assertEqual(status, 200)
        self.assertNotIn(b"private-cookie", data)
        self.assertEqual(json.loads(call("credentials?source=twitter")[1])["configured"], ["auth_token"])
        self.assertEqual(call("progress?logs=2&items=3")[0], 200)
        self.assertEqual(upstream.seen[-1][0], "/api/progress?logs=2&items=3")
        (self.asg / "downloads/test.txt").write_text("<script>inert caption</script>")
        code, data, headers = call("file?path=" + quote("/files/test.txt", safe=""))
        self.assertEqual(code, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(call("file?path=" + quote("/files/%2e%2e/secret.txt", safe=""))[0], 400)
        self.assertEqual(call("task/stop", {})[0], 200)
        upstream.code = 409
        self.assertEqual(call("task/start", {"source": "danbooru"})[0], 409)
        self.assertEqual(call("source/test", {"source": "e621"})[0], 400)
        self.client._process.poll.return_value = 1
        self.assertFalse(json.loads(call("status")[1])["running"])
        self.assertEqual(call("sources")[0], 503)


if __name__ == "__main__":
    unittest.main()
