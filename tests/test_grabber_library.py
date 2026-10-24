"""Offline multi-source, model discovery and library HTTP contract coverage."""

import json
import os
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from kohakumoss_ui.contract import grabber, grabber_models
from kohakumoss_ui.server.http import make_server
from test_grabber import GrabberCase


class MultiSourceTests(GrabberCase):
    def body(self):
        return {"sources": ["danbooru", "gelbooru"], "count": 3,
                "source_configs": {"danbooru": {"artist": "one"},
                                   "gelbooru": {"artist": "two", "user_id": "22"}}}

    def test_full_frontend_payload_normal_and_images_only(self):
        # Mirror web/grabber.js cfg + body(), not the minimal body() fixture.
        # Exercise the real merge and HTTP serialization, mocking only ASG.
        server = self.upstream()
        server.reply = {"ok": True, "sources": [{"id": "danbooru"}, {"id": "yandere"}],
                        "capabilities": {"multi_source": True, "images_only": True}}
        for images_only, include_meta in ((False, False), (False, True), (True, False)):
            with self.subTest(images_only=images_only, include_meta=include_meta):
                payload = {
                    "canonical_artist": "frontend-group", "count": 200,
                    "start_page": 1, "end_page": 0, "skip_video": True,
                    "include_artist": False, "include_meta": include_meta,
                    "images_only": images_only, "background_enabled": False,
                    "background_color": "#ffffff", "tagger_id": "none",
                    "tag_merge_mode": "native_only", "tag_format": "comma",
                    "sources": ["danbooru", "yandere"],
                    "source_configs": {
                        "danbooru": {"artist": "first_artist", "query_type": "artist", "rating": "g"},
                        "yandere": {"artist": "second_artist", "query_type": "artist", "rating": "s"},
                    },
                    "tagger_config": {},
                }
                self.assertTrue(self.client.post("task/start", payload)["ok"])
                expected = dict(payload)
                if images_only:
                    expected.pop("tagger_config")
                self.assertEqual(server.seen[-1], ("/api/start", expected))
                # Accepting include_meta must not broaden source-auth inheritance.
                for key in ("api_key", "login", "cookie", "proxy"):
                    before = len(server.seen)
                    with self.assertRaises(grabber.GrabberError):
                        self.client.post("task/start", {**payload, key: "must-stay-local"})
                    self.assertEqual(len(server.seen), before)

    def test_independent_saved_credentials_and_global_tagger_once(self):
        grabber.credentials("danbooru", {"api_key": "dan-secret", "login": "dan-login", "proxy": "http://127.0.0.1:1234"})
        grabber.credentials("gelbooru", {"api_key": "gel-secret", "user_id": "saved-user"})
        grabber.credentials("tagger:openai_compatible", {"api_key": "ai-secret", "model": "ai"})
        body = self.body()
        body.update(tagger_id="openai_compatible", tagger_config={"api_key": ""})
        body["sources"].append("danbooru")
        body["source_configs"]["danbooru"]["api_key"] = "override-secret"
        with mock.patch.object(grabber, "_saved", wraps=grabber._saved) as saved:
            result = self.client._merge(body)
        self.assertEqual(result["sources"], ["danbooru", "gelbooru"])
        self.assertEqual(saved.call_count, 3)
        dan, gel = (result["source_configs"][s] for s in result["sources"])
        self.assertEqual(dan["api_key"], "override-secret")
        self.assertEqual(gel["api_key"], "gel-secret")
        self.assertEqual(gel["user_id"], "22")
        self.assertNotIn("login", gel)
        self.assertNotIn("proxy", gel)
        self.assertNotIn("api_key", result)
        self.assertEqual(result["tagger_config"]["api_key"], "ai-secret")
        self.assertNotIn("tagger_config", dan)
        self.assertNotIn("login", body["source_configs"]["danbooru"])
        echoed = self.client._redact({"source_configs": result["source_configs"], "tagger_config": result["tagger_config"],
                                      "logs": ["override-secret gel-secret ai-secret"], "Cookie": "raw"})
        self.assertEqual(echoed, {"logs": ["[redacted] [redacted] [redacted]"]})

    def test_malformed_sources_or_nested_overrides_never_post(self):
        invalid = [{"sources": value} for value in (None, [], "danbooru", ["unknown"], [{}], ["danbooru"] * 9)]
        invalid += [{"source_configs": {}}, {"sources": ["danbooru"], "source_configs": []},
                    {**self.body(), "count": {"api_key": "nested-secret"}}]
        for key in ("api_key", "login", "cookie", "artist", "source", "proxy"):
            invalid.append({**self.body(), key: "wrong"})
        for key in ("tagger", "tagger_id", "tagger_config", "images_only", "count", "skip", "enabled", "cookie", "access_token"):
            invalid.append({"sources": ["danbooru"], "source_configs": {"danbooru": {key: "wrong"}}})
        invalid.append({"sources": ["danbooru"], "source_configs": {"pixiv": {"artist": "id"}}})
        with mock.patch.object(self.client, "_request") as request:
            for body in invalid:
                with self.subTest(body=body), self.assertRaises((ValueError, grabber.GrabberError)):
                    self.client.post("task/start", body)
            request.assert_not_called()
        for route in ("artists/search", "source/test", "tagger/test"):
            with self.assertRaises(ValueError):
                self.client.post(route, self.body())

    def test_images_only_never_loads_ai_and_nested_override_is_rejected(self):
        grabber.credentials("tagger:openai_compatible", {"api_key": "private-ai"})
        body = {**self.body(), "images_only": True, "tagger_id": "openai_compatible", "tagger_config": {"api_key": "private-input"}}
        with mock.patch.object(grabber, "_saved", wraps=grabber._saved) as saved:
            result = self.client._merge(body)
        self.assertEqual(saved.call_count, 2)
        self.assertEqual(result["tagger_id"], "none")
        self.assertNotIn("tagger_config", result)
        self.assertNotIn("private", json.dumps(result))
        body["source_configs"]["danbooru"]["images_only"] = False
        with self.assertRaises(grabber.GrabberError):
            self.client._merge(body)

    def test_loopback_start_metadata_and_auth_failure_no_silent_skip(self):
        server = self.upstream()
        self.client.post("task/start", self.body())  # Older ASG already supports multi-source.
        self.assertEqual(server.seen[-2][0], "/api/sources")
        self.assertEqual(server.seen[-1][0], "/api/start")
        metadata = {"sources": [{"id": "danbooru"}, {"id": "gelbooru", "needs_auth": True,
                     "auth_fields": [{"id": "api_key", "required": True}]}], "capabilities": {"multi_source": True}}
        server.reply = metadata
        with self.assertRaises(grabber.GrabberError) as error:
            self.client.post("task/start", self.body())
        self.assertEqual(error.exception.status, 400)
        self.assertIn("authentication", str(error.exception))
        self.assertEqual(server.seen[-1][0], "/api/sources")
        for caps in ({"multi_source": False}, {"multi_source": "true"}):
            server.reply = {**metadata, "capabilities": caps}
            with self.assertRaises(grabber.GrabberError) as error:
                self.client.post("task/start", self.body())
            self.assertEqual(error.exception.status, 409)
        server.reply = {"sources": [{"id": "danbooru"}]}
        with self.assertRaises(grabber.GrabberError):
            self.client.post("task/start", self.body())
        missing = self.body()
        missing["source_configs"]["danbooru"]["artist"] = ""
        with self.assertRaises(grabber.GrabberError):
            self.client.post("task/start", missing)


class ModelScanTests(GrabberCase):
    def test_pairing_missing_csv_and_no_service_or_model_load(self):
        models = self.asg / "models"
        models.mkdir()
        for folder, names in {"a": ["m.onnx", "m.csv", "selected_tags.csv"],
                              "b": ["m.onnx", "m.csv"], "c": ["m.onnx", "unrelated.csv"],
                              ".hidden": ["secret.onnx"]}.items():
            sub = models / folder
            sub.mkdir()
            for name in names:
                (sub / name).write_bytes(b"not a real model")
        (models / "link.onnx").symlink_to(models / "a/m.onnx")
        (models / "linked-dir").symlink_to(models / "a", target_is_directory=True)
        (models / "c/selected_tags.csv").symlink_to(models / "a/selected_tags.csv")
        with mock.patch.object(self.client, "_request") as request, mock.patch.object(grabber.subprocess, "Popen") as popen:
            response = self.client.post("models/scan", {})
            request.assert_not_called()
            popen.assert_not_called()
        self.assertFalse(response["truncated"])
        self.assertEqual(len(response["models"]), 3)
        rows = {os.path.basename(os.path.dirname(m["model_path"])): m for m in response["models"]}
        self.assertEqual(rows["a"]["tags_path"], str(models / "a/selected_tags.csv"))
        self.assertEqual(rows["b"]["tags_path"], str(models / "b/m.csv"))
        self.assertIsNone(rows["c"]["tags_path"])
        grabber.credentials("tagger:local_onnx", {"model_path": str(models / "b/m.onnx"), "tags_path": str(models / "b/m.csv")})
        self.assertEqual(self.client.post("models/scan", {})["directory"], str(models / "b"))
        self.assertEqual(grabber.credentials("tagger:local_onnx")["values"]["tags_path"], str(models / "b/m.csv"))

    def test_missing_invalid_directory_and_symlink_root(self):
        for body, code in (({}, 404), ({"directory": []}, 400), ({"directory": ""}, 400),
                           ({"directory": None}, 400), ({"directory": "\x00"}, 400),
                           ({"directory": str(self.asg / "app.py")}, 404)):
            with self.subTest(body=body), self.assertRaises(grabber.GrabberError) as error:
                self.client.post("models/scan", body)
            self.assertEqual(error.exception.status, code)
        self.assertFalse((self.asg / "models").exists())
        link = self.asg / "link"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(grabber.GrabberError) as error:
            self.client.post("models/scan", {"directory": str(link)})
        self.assertEqual(error.exception.status, 400)

    def test_depth_entry_model_and_time_limits(self):
        models = self.asg / "models"
        deep = models / "a/b/c"
        deep.mkdir(parents=True)
        (deep / "hidden.onnx").write_bytes(b"")
        for i in range(5):
            (models / (str(i) + ".onnx")).write_bytes(b"")
        response = self.client.post("models/scan", {})
        self.assertTrue(response["truncated"])
        self.assertEqual(len(response["models"]), 5)
        for name in ("MAX_ENTRIES", "MAX_MODELS"):
            with mock.patch.object(grabber_models, name, 2):
                response = self.client.post("models/scan", {})
            self.assertTrue(response["truncated"])
            self.assertLessEqual(len(response["models"]), 2)
        with mock.patch.object(grabber_models, "MAX_SECONDS", 0):
            response = self.client.post("models/scan", {})
        self.assertTrue(response["truncated"])
        self.assertEqual(response["models"], [])


class LibraryTests(GrabberCase):
    def enabled(self):
        server = self.upstream()
        server.reply = {"ok": True, "capabilities": {"library": True, "retag": True}}
        return server

    def body(self, **overrides):
        return {"group": "example", "mode": "merge", "tagger_id": "local_onnx", **overrides}

    def test_fixed_paths_pagination_and_escaped_group(self):
        server = self.enabled()
        self.client.get("library/groups", {})
        self.assertEqual(server.seen[-1], ("/api/library/groups", None))
        self.client.get("library/items", {"group": ["hello &limit=999#x"], "offset": ["3"]})
        parsed = urlsplit(server.seen[-1][0])
        self.assertEqual(parsed.path, "/api/library/items")
        self.assertEqual(parse_qs(parsed.query), {"group": ["hello &limit=999#x"], "offset": ["3"], "limit": ["60"]})
        self.client.get("library/retag/progress", {})
        self.assertEqual(server.seen[-1], ("/api/library/retag/progress", None))
        self.client.post("library/retag/stop", {"id": "job-123"})
        self.assertEqual(server.seen[-1], ("/api/library/retag/stop", {"id": "job-123"}))
        for group in ("../secret", "/absolute", "a/b", "a\\b", "%2e%2e", ".hidden", "\x00", ""):
            with self.subTest(group=group), self.assertRaises(grabber.GrabberError):
                self.client.get("library/items", {"group": [group]})
        for key, value in (("offset", "-1"), ("offset", "1.5"), ("limit", "101"), ("limit", "0")):
            with self.assertRaises(grabber.GrabberError):
                self.client.get("library/items", {"group": ["ok"], key: [value]})

    def test_retag_only_selected_tagger_never_source_auth_or_images_only(self):
        server = self.enabled()
        grabber.credentials("danbooru", {"api_key": "source-secret"})
        grabber.credentials("tagger:openai_compatible", {"api_key": "ai-secret", "endpoint": "http://127.0.0.1:8000/v1"})
        grabber.credentials("tagger:local_onnx", {"model_path": "/tmp/model.onnx", "tags_path": "/tmp/model.csv"})
        for mode in ("merge", "replace", "missing"):
            self.client.post("library/retag/start", self.body(mode=mode, confirm_replace=mode == "replace"))
            path, body = server.seen[-1]
            self.assertEqual(path, "/api/library/retag/start")
            self.assertNotIn("images_only", body)
            self.assertNotIn("secret", json.dumps(body))
            self.assertEqual(body["tagger_config"]["model_path"], "/tmp/model.onnx")
        self.client.post("library/retag/start", self.body(tagger_id="openai_compatible", confirm_remote=True))
        body = server.seen[-1][1]
        self.assertEqual(body["tagger_config"]["api_key"], "ai-secret")
        self.assertNotIn("source-secret", json.dumps(body))
        self.assertNotIn("model_path", body["tagger_config"])

    def test_confirmations_invalid_modes_and_none_reject_before_request(self):
        invalid = [{"mode": "replace"}, {"mode": "other"}, {"mode": []}, {"tagger_id": "none"},
                   {"tagger_id": "openai_compatible"}, {"tagger_id": "openai_compatible", "confirm_remote": "true"},
                   {"mode": "replace", "confirm_replace": 1}, {"tag_format": "html"},
                   {"images_only": True}, {"source": "danbooru"}, {"api_key": "source-secret"}]
        with mock.patch.object(self.client, "_request") as request:
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaises(grabber.GrabberError):
                    self.client.post("library/retag/start", self.body(**changes))
            request.assert_not_called()

    def test_old_service_rejected_for_every_library_route(self):
        server = self.upstream()
        calls = [("library/groups", None), ("library/items", {"group": ["example"]}),
                 ("library/retag/progress", None), ("library/retag/start", self.body()), ("library/retag/stop", {})]
        for route, body in calls:
            with self.subTest(route=route), self.assertRaises(grabber.GrabberError) as error:
                if route.endswith(("start", "stop")):
                    self.client.post(route, body)
                else:
                    self.client.get(route, body or {})
            self.assertEqual(error.exception.status, 409)
            self.assertIn("restart ArtistSourceGrabber", str(error.exception))
        self.assertTrue(all(path == "/api/sources" for path, _ in server.seen))

    def test_http_new_routes_use_existing_token_and_offline_scan(self):
        server = make_server(self.root, "127.0.0.1", 0, "test-token")
        server.RequestHandlerClass.api.grabber = self.client
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = build_opener(ProxyHandler({}))
        routes = [("models/scan", {}), ("library/groups", None), ("library/items?group=x", None),
                  ("library/retag/progress", None), ("library/retag/start", self.body()), ("library/retag/stop", {})]
        def call(route, body, token):
            req = Request("http://127.0.0.1:%s/api/grabber/%s" % (server.server_address[1], route),
                          data=None if body is None else json.dumps(body).encode(),
                          headers={"X-UI-Token": token, "Content-Type": "application/json"})
            try:
                response = opener.open(req, timeout=3)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.loads(response.read())
        for route, body in routes:
            for token in ("", "wrong"):
                self.assertEqual(call(route, body, token)[0], 403)
        (self.asg / "models").mkdir()
        code, response = call("models/scan", {}, "test-token")
        self.assertEqual(code, 200)
        self.assertEqual(response["models"], [])
        self.assertEqual(call("library/groups", None, "test-token")[0], 503)
        self.assertEqual(call("library/retag/start", self.body(mode="replace"), "test-token")[0], 400)


if __name__ == "__main__":
    unittest.main()
