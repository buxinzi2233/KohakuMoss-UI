"""Civitai name matching, credential masking and the cached sync path."""

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.server import civitai
from kohakumoss_ui.store import profile


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def read(self):
        return self._body

    def close(self):
        self.closed = True


def snapshot(name, model_id=1, **extra):
    row = {
        "id": model_id,
        "name": name,
        "normalized": civitai.normalize(name),
        "url": "https://civitai.com/models/" + str(model_id),
        "type": "LORA",
        "createdAt": "2024-01-01T00:00:00.000Z",
    }
    row.update(extra)
    return row


class NormalizeTest(unittest.TestCase):
    def test_normalize_folds_symbols_and_extensions(self):
        self.assertEqual(civitai.normalize("Kohaku Style V2"), "kohaku-style-v2")
        self.assertEqual(civitai.normalize("kohaku_style_v2.safetensors"), "kohaku-style-v2")
        self.assertEqual(civitai.normalize("  --Moss//Base--  "), "moss-base")
        self.assertEqual(civitai.normalize(""), "")
        self.assertEqual(civitai.normalize("kohaku.style"), "kohaku-style")

    def test_local_name_strips_the_export_suffix(self):
        self.assertEqual(civitai.local_name("moss-demo_lora.safetensors"), "moss-demo")
        self.assertEqual(civitai.local_name("moss-demo_lora"), "moss-demo")
        self.assertEqual(civitai.local_name("moss_demo"), "moss-demo")


class MatchTest(unittest.TestCase):
    def test_hit_marks_published(self):
        models = [snapshot("Kohaku Style", 11)]
        found = civitai.match_local({"kohaku-style": ["run-a"]}, models)["kohaku-style"]
        self.assertEqual(found["state"], "published")
        self.assertEqual(found["modelId"], 11)
        self.assertEqual(found["runs"], ["run-a"])
        self.assertFalse(found["ambiguous"])

    def test_miss_is_a_draft(self):
        found = civitai.match_local({"nobody": ["run-b"]}, [])["nobody"]
        self.assertEqual(found["state"], "draft")
        self.assertIsNone(found["modelId"])
        self.assertFalse(found["ambiguous"])

    def test_two_hits_are_ambiguous(self):
        models = [snapshot("Kohaku Style", 1), snapshot("kohaku  style", 2)]
        found = civitai.match_local({"kohaku-style": ["run-a"]}, models)["kohaku-style"]
        self.assertTrue(found["ambiguous"])
        self.assertEqual(found["modelId"], 1)
        self.assertEqual(found["state"], "unknown")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runs = self.base / "runs"
        self.runs.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {
                "KOHAKUMOSS_UI_STATE": str(self.base / "state"),
                "KOHAKUMOSS_RUNS": str(self.runs),
            },
        )
        self.env.start()
        self.root = TrainerRoot(self.base)

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def test_credentials_never_echo_the_key(self):
        saved = civitai.save_credentials("moss", "secret-1234")
        self.assertTrue(saved["hasKey"])
        self.assertEqual(saved["keyHint"], "****1234")
        status = civitai.status()
        self.assertEqual(status["username"], "moss")
        self.assertNotIn("apiKey", status)
        self.assertNotIn("apiKey", saved)
        stored = json.loads((self.base / "state" / "civitai.json").read_text())
        self.assertEqual(stored["apiKey"], "secret-1234")
        self.assertEqual(civitai.mask_key("ab"), "****")
        self.assertEqual(civitai.mask_key(""), "")
        with self.assertRaises(ValueError):
            civitai.save_credentials("")
        cleared = civitai.delete_credentials()
        self.assertFalse(cleared["hasKey"])
        self.assertEqual(cleared["username"], "")

    def test_switching_account_drops_the_old_snapshot(self):
        civitai.save_credentials("moss", "secret-1234")
        client = lambda request, timeout=None: FakeResponse(
            {"items": [snapshot("Kohaku Style", 5)], "metadata": {}}
        )
        first = civitai.sync(self.root, opener=client, now=1000.0)
        self.assertEqual(len(first["models"]), 1)
        self.assertEqual(first["error"], "")
        moved = civitai.save_credentials("someone-else")
        self.assertFalse(moved["hasKey"])
        self.assertEqual(moved["lastSync"], 0.0)

    def test_sync_paginates_until_metadata_stops(self):
        civitai.save_credentials("moss")
        seen = []

        def client(request, timeout=None):
            seen.append(request.full_url)
            if "page=1" in request.full_url:
                return FakeResponse(
                    {"items": [snapshot("A", 1)], "metadata": {"nextPage": 2}}
                )
            return FakeResponse({"items": [snapshot("B", 2)]})

        payload = civitai.sync(self.root, opener=client)
        self.assertEqual([row["id"] for row in payload["models"]], [1, 2])
        self.assertEqual(len(seen), 2)
        self.assertIn("types=LORA", seen[0])
        self.assertIn("username=moss", seen[0])

    def test_cached_snapshot_skips_the_network(self):
        civitai.save_credentials("moss")
        payload = {"items": [snapshot("A", 1)], "metadata": {}}

        def client(request, timeout=None):
            return FakeResponse(payload)

        first = civitai.sync(self.root, opener=client, now=5000.0)
        self.assertFalse(first["cached"])
        calls = []

        def offline(request, timeout=None):
            calls.append(request)
            raise AssertionError("cache must not hit the network")

        second = civitai.sync(self.root, opener=offline, now=5000.0 + 60)
        self.assertTrue(second["cached"])
        self.assertEqual(calls, [])
        third = civitai.sync(self.root, opener=client, refresh=True, now=5000.0)
        self.assertFalse(third["cached"])

    def test_http_error_keeps_the_old_snapshot(self):
        civitai.save_credentials("moss", "secret-1234")
        good = {"items": [snapshot("Kohaku Style", 9)], "metadata": {}}
        civitai.sync(self.root, opener=lambda request, timeout=None: FakeResponse(good), now=100.0)

        def unauthorized(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        payload = civitai.sync(self.root, opener=unauthorized, refresh=True, now=200.0)
        self.assertEqual(payload["error"], "civitai returned HTTP 401")
        self.assertEqual([row["id"] for row in payload["models"]], [9])
        self.assertTrue(payload["cached"])

    def test_timeout_and_secret_scrubbing(self):
        civitai.save_credentials("moss", "secret-1234")

        def slow(request, timeout=None):
            raise TimeoutError("timed out")

        timed = civitai.sync(self.root, opener=slow)
        self.assertEqual(timed["error"], "civitai request timed out")

        def leaky(request, timeout=None):
            raise ValueError("boom secret-1234")

        payload = civitai.sync(self.root, opener=leaky)
        self.assertNotIn("secret-1234", payload["error"])
        self.assertIn("***", payload["error"])
        civitai.delete_credentials()
        with self.assertRaises(ValueError):
            civitai.sync(self.root)

    def test_publish_map_ignores_legacy_manual_marks(self):
        (self.runs / "kohaku-style").mkdir()
        (self.runs / "kohaku-style" / "log.txt").write_text("hello\n", encoding="utf-8")
        models = [snapshot("Kohaku Style", 5)]
        merged = civitai.publish_map(self.root, models)
        self.assertEqual(merged["kohaku-style"]["state"], "published")
        self.assertFalse(merged["kohaku-style"]["manual"])
        profile.set_publish("kohaku-style", {"state": "draft", "manual": True})
        merged = civitai.publish_map(self.root, models)
        self.assertEqual(merged["kohaku-style"]["state"], "published")
        self.assertFalse(merged["kohaku-style"]["manual"])
        # Existing metadata stays on disk; it cannot override API evidence.
        self.assertEqual(profile.publish()["kohaku-style"]["state"], "draft")

    def test_empty_success_is_cached_and_first_failure_is_unknown(self):
        run = self.runs / "test-model"
        run.mkdir()
        (run / "log.txt").write_text("test")
        civitai.save_credentials("moss")
        def offline(request, timeout=None):
            raise TimeoutError("offline")
        failed = civitai.sync(self.root, opener=offline, now=100.0)
        self.assertEqual(failed["publish"]["test-model"]["state"], "unknown")
        self.assertTrue(failed["error"])
        civitai.sync(self.root, opener=lambda *a, **k: FakeResponse({"items": []}), now=200.0)
        cached = civitai.sync(self.root, opener=offline, now=201.0)
        self.assertEqual(cached["error"], "")
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["publish"]["test-model"]["state"], "draft")

    def test_api_queries_account_automatically_and_refresh_bypasses_cache(self):
        from kohakumoss_ui.server.http import Api
        run = self.runs / "kohaku-style"
        run.mkdir()
        (run / "log.txt").write_text("test")
        profile.set_publish("kohaku-style", {"state": "published", "modelId": 999, "manual": True})
        api = Api(self.root, "test")
        with mock.patch.object(civitai, "_get_json") as fetch:
            unconfigured = api.get_publish()
            self.assertIsNone(unconfigured["count"])
            self.assertEqual(unconfigured["publish"]["kohaku-style"]["state"], "unknown")
            fetch.assert_not_called()
        civitai.save_credentials("moss")
        with mock.patch.object(civitai, "_get_json", return_value={"items": [snapshot("Kohaku Style", 42)]}) as fetch:
            found = api.get_publish()
            self.assertEqual(found["count"], 1)
            self.assertEqual(found["publish"]["kohaku-style"]["modelId"], 42)
            self.assertEqual(fetch.call_count, 1)
            api.get_publish()
            self.assertEqual(fetch.call_count, 1)
            api.get_publish({"refresh": ["1"]})
            self.assertEqual(fetch.call_count, 2)

    def test_published_count_deduplicates_aliases_and_skips_ambiguous(self):
        marks = {"a": {"state": "published", "modelId": 1},
                 "b": {"state": "published", "modelId": 1},
                 "c": {"state": "unknown", "modelId": 2},
                 "d": {"state": "published", "modelId": 3, "ambiguous": True}}
        self.assertEqual(civitai.published_count(marks), 1)

    def test_account_change_during_background_query_cannot_restore_old_credentials(self):
        civitai.save_credentials("old", "old-secret")
        def switch_account(request, timeout=None):
            civitai.save_credentials("new", "new-secret")
            return FakeResponse({"items": [snapshot("Old model", 42)]})
        result = civitai.sync(self.root, opener=switch_account)
        self.assertTrue(result["error"])
        self.assertEqual(civitai.status()["username"], "new")
        self.assertEqual(civitai.status()["lastSync"], 0.0)
        self.assertEqual(civitai._read()["models"], [])


if __name__ == "__main__":
    unittest.main()
