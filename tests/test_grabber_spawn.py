"""Real child-process regression, using an offline temporary ASG substitute."""

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kohakumoss_ui.contract import grabber
from kohakumoss_ui.contract.root import TrainerRoot


# Also used by the disposable /tmp integration fixture; never imports real ASG.
FAKE_APP = r'''
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def network_guard(event, args):
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise PermissionError("offline fixture forbids external network")

sys.addaudithook(network_guard)
ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads" / "fixture_artist"
DOWNLOADS.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
TASK = None
LAST = {}
AUTH = {
    "danbooru": [("login", False), ("api_key", True)],
    "twitter": [("auth_token", True), ("ct0", True), ("x_cookies_file", False)],
    "openverse": [],
    "pixiv": [("access_token", True), ("phpsessid", True)],
    "gelbooru": [("user_id", False), ("api_key", True)],
    "safebooru": [], "konachan": [], "yandere": [],
}
LABELS = {"danbooru": "Danbooru", "twitter": "X / Twitter", "openverse": "Openverse",
          "pixiv": "Pixiv", "gelbooru": "Gelbooru", "safebooru": "Safebooru",
          "konachan": "Konachan", "yandere": "yande.re"}
SOURCES = [{"id": s, "label": LABELS[s], "needs_auth": s in {"twitter", "gelbooru"},
            "supports_artist_search": s != "twitter", "can_count": s not in {"twitter", "openverse"},
            "description": "OFFLINE fixture - no source network or login",
            "auth_fields": [{"id": k, "label": k, "secret": secret,
                             "required": s == "gelbooru"} for k, secret in fields],
            "ratings": [["", "All"], ["safe", "Safe"]] if s == "pixiv" else
                       [["", "All"], ["g", "General"], ["s", "Sensitive"], ["q", "Questionable"], ["e", "Explicit"]],
            "extra_fields": ([{"id": "openverse_license", "label": "License", "type": "select", "options": [["all", "All"], ["commercial", "Commercial"]]}] if s == "openverse" else
                [{"id": "x_mode", "label": "X mode", "type": "select", "options": [["media_user", "Media"], ["tweets_user", "Tweets"], ["likes", "Likes"]]},
                 {"id": "x_cookies_from_browser", "label": "Browser", "type": "select", "options": [["", "None"], ["firefox", "Firefox"]]},
                 {"id": "x_browser_profile", "label": "Profile", "type": "text"}] if s == "twitter" else [])}
           for s, fields in AUTH.items()]


def png(number):
    width, height = 360, 240
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            band = (x // 45 + y // 40 + number) % 3
            pixels.extend(((35 + x // 3 + number * 20) % 220,
                           (80 + y // 3 + band * 30) % 240,
                           (145 + number * 25 + band * 15) % 240))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")

for number in range(1, 9):
    (DOWNLOADS / ("fixture_%d.png" % number)).write_bytes(png(number))
    (DOWNLOADS / ("fixture_%d.txt" % number)).write_text("abstract, color study, fixture_%d" % number)


def snapshot(logs=0, items=0):
    with LOCK:
        if TASK is None:
            return {"status": "idle"}
        elapsed = time.monotonic() - TASK["started"]
        count = TASK["count"]
        step = 5 if "slow" in TASK["artist"].lower() else 1.5
        done = min(count, int(elapsed / step))
        status, error = "running", ""
        if "error" in TASK["artist"].lower() and elapsed > 2:
            status, error, done = "error", "Offline fixture simulated source failure", min(done, 1)
        elif TASK.get("stop_at"):
            done = min(count, int((TASK["stop_at"] - TASK["started"]) / step))
            if time.monotonic() - TASK["stop_at"] >= 1:
                status = "stopped"
        elif done >= count:
            status = "done"
        all_items = []
        for n in range(1, done + 1):
            index = (n - 1) % 8 + 1
            base = "/files/fixture_artist/fixture_%d" % index
            all_items.append({"id": str(n), "source": TASK["source"], "url": "",
                "ext": "png", "status": "ok", "filename": "fixture_%d.png" % index,
                "preview_url": base + ".png", "image_url": base + ".png", "caption_url": base + ".txt",
                "native_tags": ["abstract", "color_study"], "generated_tags": ["offline_fixture"],
                "final_tags": ["abstract", "color_study", "offline_fixture"], "tagger_id": TASK["tagger_id"],
                "tag_status": "merged", "tag_error": "", "tag_merge_mode": "native_plus_tagger", "background_applied": False})
        all_logs = ["[00:00:00] Offline fixture accepted single-source task"]
        all_logs += ["[00:00:%02d] Saved fixture image %d" % (n, n) for n in range(1, done + 1)]
        if TASK.get("stop_at"):
            all_logs.append("[00:00:00] Stop requested; cooperative cleanup")
        if status != "running":
            all_logs.append("[00:00:00] Task " + status)
        return {"status": status, "error": error, "source": TASK["source"], "source_label": LABELS[TASK["source"]],
            "artist": TASK["artist"], "canonical_artist": TASK["artist"], "folder": "fixture_artist",
            "site_total": count, "target": count, "done": done, "skipped": 0, "failed": int(status == "error"),
            "logs": all_logs[logs:], "log_count": len(all_logs), "items": all_items[items:], "item_count": len(all_items),
            "active_source": TASK["source"] if status == "running" else "",
            "sources": [{"id": TASK["source"], "label": LABELS[TASK["source"]], "artist": TASK["artist"],
                         "artist_key": TASK["artist"], "status": status, "error": error,
                         "site_total": count, "target": count, "done": done, "skipped": 0, "failed": int(status == "error")}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/sources":
            self.reply({"sources": SOURCES})
        elif parsed.path == "/api/progress":
            self.reply(snapshot(int(query.get("logs", [0])[0]), int(query.get("items", [0])[0])))
        elif parsed.path == "/api/fixture/received":
            self.reply(LAST)
        else:
            self.reply({"error": "offline fixture route not found"}, 404)

    def do_POST(self):
        global TASK, LAST
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        def digest(value):
            return hashlib.sha256(str(value).encode()).hexdigest()
        LAST = {"source_digest": digest(body.get("api_key", "")),
                "tagger_digest": digest((body.get("tagger_config") or {}).get("api_key", "")),
                "artist": body.get("artist", ""), "tagger_id": body.get("tagger_id", "none")}
        if self.path == "/api/start":
            with LOCK:
                if snapshot()["status"] in {"running", "preparing"}:
                    self.reply({"ok": False, "error": "Offline fixture task already running"}, 409)
                    return
                TASK = {"source": body.get("source", "danbooru"), "artist": body.get("artist", "fixture_artist"),
                        "count": min(20, max(1, int(body.get("count", 0) or 8))),
                        "tagger_id": body.get("tagger_id", "none"), "started": time.monotonic()}
            self.reply({"ok": True})
        elif self.path == "/api/stop":
            with LOCK:
                if TASK and snapshot()["status"] == "running" and not TASK.get("stop_at"):
                    TASK["stop_at"] = time.monotonic()
            self.reply({"ok": True})
        elif self.path == "/api/shutdown":
            self.reply({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        elif self.path == "/api/artists/search":
            query = body.get("query", "fixture")
            self.reply({"ok": True, "artists": [{"id": "1", "name": query, "site": body.get("source", "danbooru"),
                "profile_url": "", "post_count": 8, "other_names": ["Offline candidate"], "is_banned": False,
                "x_handles": ["fixture_artist"], "x_user_ids": ["123456"], "pixiv_user_ids": ["123456"], "urls": []}]})
        elif self.path in {"/api/source/test", "/api/tagger/test"}:
            fail = body.get("artist") == "failtest" or (body.get("tagger_config") or {}).get("model") == "failtest"
            self.reply({"ok": False, "error": "Offline fixture test failure"} if fail else {"ok": True, "message": "Offline fixture ready; no external network used"})
        elif "/session/" in self.path:
            self.reply({"ok": True, "running": True, "logged_in": True, "message": "Offline simulated session; no browser or cookies accessed"})
        else:
            self.reply({"error": "offline fixture route not found"}, 404)

assert "--no-browser" in sys.argv
assert os.environ.get("PYTHONUNBUFFERED") == "1"
server = None
for port in range(8710, 8720):
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        break
    except OSError:
        pass
if server is None:
    raise SystemExit("No free fixture port in 8710..8719")
(ROOT / "process.json").write_text(json.dumps({"pid": os.getpid(), "port": server.server_address[1]}))
print("  \u754c\u9762\u5730\u5740\uff1ahttp://127.0.0.1:%d" % server.server_address[1], flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
'''


class RealSpawnTests(unittest.TestCase):
    def test_real_child_banner_readiness_and_credentials_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            asg = base / "ArtistSourceGrabber"
            asg.mkdir()
            (asg / "app.py").write_text(FAKE_APP)
            env = {"KOHAKUMOSS_GRABBER_ROOT": str(asg),
                   "KOHAKUMOSS_GRABBER_PYTHON": sys.executable,
                   "KOHAKUMOSS_UI_STATE": str(base / "state")}
            with mock.patch.dict(os.environ, env):
                client = grabber.Grabber(TrainerRoot(base / "trainer"))
                try:
                    self.assertFalse(client.status()["running"])
                    state = client.start_service()
                    self.assertTrue(state["running"], state)
                    self.assertTrue(state["managed"])
                    self.assertIn(state["port"], range(8710, 8720))
                    identity = json.loads((asg / "process.json").read_text())
                    self.assertEqual(state["pid"], identity["pid"])
                    self.assertEqual(state["port"], identity["port"])
                    self.assertEqual(client.start_service()["pid"], state["pid"])
                    self.assertEqual(len(client.get("sources", {})["sources"]), 8)
                    self.assertEqual(client.get("progress", {})["status"], "idle")
                    grabber.credentials("danbooru", {"login": "test", "api_key": "saved-source"})
                    grabber.credentials("tagger:openai_compatible", {"endpoint": "http://127.0.0.1:9999/v1", "model": "fixture", "api_key": "saved-tagger"})
                    for source_key, tagger_key, wanted_source, wanted_tagger in [
                        ("", "", "saved-source", "saved-tagger"),
                        ("explicit-source", "explicit-tagger", "explicit-source", "explicit-tagger"),
                    ]:
                        client.post("source/test", {"source": "danbooru", "api_key": source_key,
                            "artist": "test", "rating": "g", "count": 2,
                            "tagger_id": "openai_compatible", "tagger_config": {"api_key": tagger_key, "model": "updated-model"}})
                        received = client._request("/api/fixture/received")
                        self.assertEqual(received["source_digest"], hashlib.sha256(wanted_source.encode()).hexdigest())
                        self.assertEqual(received["tagger_digest"], hashlib.sha256(wanted_tagger.encode()).hexdigest())
                    self.assertTrue(client.post("task/start", {"source": "danbooru", "artist": "slow_demo", "count": 3})["ok"])
                    self.assertEqual(client.get("progress", {})["status"], "running")
                    with self.assertRaises(grabber.GrabberError) as conflict:
                        client.post("task/start", {"source": "danbooru", "artist": "other"})
                    self.assertEqual(conflict.exception.status, 409)
                    self.assertTrue(client.post("task/stop", {})["ok"])
                    self.assertEqual(client.get("progress", {})["status"], "running")
                    def terminal():
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            result = client.get("progress", {})
                            if result["status"] not in {"running", "preparing"}:
                                return result
                            time.sleep(0.1)
                        self.fail("fake task did not reach terminal state")
                    self.assertEqual(terminal()["status"], "stopped")
                    client.post("task/start", {"source": "danbooru", "artist": "demo", "count": 1})
                    finished = terminal()
                    self.assertEqual(finished["status"], "done")
                    self.assertEqual(finished["item_count"], 1)
                    self.assertEqual(client.file(finished["items"][0]["preview_url"])[1], "image/png")
                    client.post("task/start", {"source": "danbooru", "artist": "error_demo", "count": 8})
                    self.assertEqual(terminal()["status"], "error")
                    client.close()
                    self.assertIsNone(client._process.poll(), "closing UI must not kill ASG")
                    self.assertEqual(client._request("/api/progress")["source"], "danbooru")
                finally:
                    if client._process is not None and client._process.poll() is None:
                        client._request("/api/shutdown", {})
                        client._process.wait(timeout=5)
                    client.close()


if __name__ == "__main__":
    unittest.main()
