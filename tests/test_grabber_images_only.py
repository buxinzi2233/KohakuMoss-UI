"""Images-only adapter tests; temporary credentials and loopback fake ASG only."""
import json
import unittest
from unittest import mock

from kohakumoss_ui.contract import grabber
from test_grabber import GrabberCase


class ImagesOnlyTests(GrabberCase):
    def test_merge_normalizes_and_drops_stale_ai_settings_without_loading_them(self):
        grabber.credentials("tagger:openai_compatible", {"api_key": "saved-ai-secret", "model": "saved"})
        grabber.credentials("danbooru", {"api_key": "saved-source-secret"})
        body = {"source": "danbooru", "images_only": "true", "tagger_id": "invalid",
                "tagger_type": {"broken": True}, "tagger": "invalid",
                "tagger_config": {"api_key": "request-ai-secret"}, "llm_api_key": "legacy-secret",
                "onnx_model_path": "/missing/model.onnx"}
        saved = grabber._saved
        def source_only(source):
            self.assertEqual(source, "danbooru")
            return saved(source)
        with mock.patch.object(grabber, "_saved", side_effect=source_only):
            result = self.client._merge(body)
        self.assertIs(result["images_only"], True)
        self.assertEqual(result["tagger_id"], "none")
        self.assertEqual(result["api_key"], "saved-source-secret")
        for key in ("tagger_type", "tagger", "tagger_config", "llm_api_key", "onnx_model_path"):
            self.assertNotIn(key, result)
        self.assertNotIn("ai-secret", json.dumps(result))
        self.assertEqual(body["tagger_id"], "invalid")

    def test_boolean_false_and_invalid_values(self):
        self.assertIs(self.client._merge({"images_only": "false"})["images_only"], False)
        self.assertIs(self.client._merge({})["images_only"], False)
        for value in (None, 0, 1, "invalid", "", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client._merge({"images_only": value})

    def test_old_asg_is_rejected_without_posting_start(self):
        server = self.upstream()
        for capabilities in (None, {}, {"images_only": False}, {"images_only": "true"}, [], {"images_only": 1}):
            with self.subTest(capabilities=capabilities):
                server.reply = {"sources": [], "capabilities": capabilities}
                with self.assertRaises(grabber.GrabberError) as error:
                    self.client.post("task/start", {"source": "danbooru", "images_only": True})
                self.assertEqual(error.exception.status, 409)
                self.assertIn("restart ArtistSourceGrabber", str(error.exception))
                self.assertEqual(server.seen[-1], ("/api/sources", None))
        self.assertFalse(any(path == "/api/start" for path, _ in server.seen))
        self.assertFalse(self.client._task_lock.locked())

    def test_supported_asg_checks_then_submits_and_metadata_passes_through(self):
        server = self.upstream()
        server.reply = {"ok": True, "sources": [{"id": "danbooru", "query_types": ["artist", "character", "tag"],
                                                "search_query_types": ["artist", "character", "tag"]}],
                        "capabilities": {"images_only": True}}
        self.assertEqual(self.client.get("sources", {}), server.reply)
        self.assertTrue(self.client.post("task/start", {"source": "danbooru", "images_only": True,
                                                      "tagger_type": "invalid"})["ok"])
        self.assertEqual(server.seen[-2], ("/api/sources", None))
        self.assertEqual(server.seen[-1], ("/api/start", {"source": "danbooru", "images_only": True,
                                                       "tagger_id": "none"}))

    def test_default_start_does_not_require_capability(self):
        server = self.upstream()
        self.client.post("task/start", {"source": "danbooru", "images_only": "false"})
        self.assertEqual(server.seen, [("/api/start", {"source": "danbooru", "images_only": False})])


if __name__ == "__main__":
    unittest.main()
