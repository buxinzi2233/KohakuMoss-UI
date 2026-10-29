"""Real raster/native-data previews, immutable fences and HTTP authorization."""

import base64
import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from kohakumoss_ui.contract.gbc import GbcService
from kohakumoss_ui.contract.gbc_data import DataError, call
from kohakumoss_ui.contract.gbc_schema import GbcError
from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.server.http import make_server
from test_gbc_data import PYTHON, graph, png


class GbcMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="gbc-media-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        images = cls.base / "images"
        images.mkdir()
        records, graphs = [], []
        for i in range(3):
            image = images / f"image-{i}.png"
            image.write_bytes(png(256, 128))
            value = graph(image)
            value["img_size"] = [256, 128]
            value["vertices"][1]["bbox"].update(left=0.25, top=0.25, right=0.75, bottom=0.75)
            value["vertices"][0]["descs"][0]["text"] = 'Literal <img src=x onerror="alert(1)"> & safe text.'
            graphs.append(value)
            records.append({"key": f"sample-{i}", "image": str(image), "meta": {"rating": "general"}})
        graph_file = cls.base / "graphs.jsonl"
        graph_file.write_text("".join(json.dumps(g) + "\n" for g in graphs))
        cls.dataset = cls.base / "dataset"
        call(PYTHON, {"action": "pack", "graphs": str(graph_file), "imageRoot": str(images),
                      "output": str(cls.dataset), "records": records, "perShard": 2})

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(dir=self.base, prefix="case-"))
        self.service = GbcService(TrainerRoot(self.case / "trainer"), state=self.case / "state")
        self.addCleanup(self.service.close)
        output = self.case / "output"
        output.mkdir()
        settings = self.service.post("settings", {"apiVersion": "gbc-ui-v1", "requestId": "settings",
            "expectedRevision": 0, "roots": [
                {"path": str(self.dataset), "purpose": "input", "confirmTrust": True},
                {"path": str(output), "purpose": "output", "confirmTrust": True}],
            "runtimes": [{"python": str(PYTHON), "role": "data", "confirmExecute": True}]})[1]["data"]["settings"]
        # A real copied/validated native package, not a mocked data worker.
        self.runtime = settings["runtimes"][0]["runtimeId"]
        from kohakumoss_ui.contract import gbc_storage_files as files
        copied = self.case / "registered"
        shutil.copytree(self.dataset, copied)
        manifest, identities = files.snapshot(copied)
        result = call(PYTHON, {"action": "validate", "path": str(copied)})
        self.service._register_dataset("dataset_test", copied, manifest, result, identities)
        self.query = {"datasetId": "dataset_test", "dataRuntimeId": self.runtime,
                      "shard": "shard_00000", "key": "sample-0"}
        self.copied = copied

    def media(self, **changes):
        return self.service.get("datasets/media", {**self.query, **changes})[1]["data"]["media"]

    def test_real_native_two_shards_raster_and_bboxes(self):
        from PIL import Image
        for key, shard in (("sample-0", "shard_00000"), ("sample-2", "shard_00001")):
            for size in ("thumb", "detail"):
                media = self.media(key=key, shard=shard, size=size)
                self.assertEqual((media["width"], media["height"]), (256, 128))
                self.assertEqual(media["mimeType"], "image/png")
                with Image.open(io.BytesIO(base64.b64decode(media["base64"], validate=True))) as image:
                    self.assertEqual(image.getpixel((100, 80)), (32, 120, 200, 255))
                    self.assertFalse(image.info)
        page = self.service.get("datasets/page", {"datasetId": "dataset_test", "dataRuntimeId": self.runtime})[1]["data"]
        self.assertIn("<img", page["items"][0]["graph"]["nodes"][0]["descriptions"][0]["text"])
        self.assertEqual(page["items"][0]["graph"]["nodes"][1]["displayBbox"]["left"], 0.25)
        self.assertTrue(self.service.get("capabilities")[1]["data"]["features"]["media"])

    def test_authorized_ids_fields_and_no_path_proxy(self):
        for changes, code in (({"datasetId": "unknown"}, "NOT_FOUND"),
                              ({"shard": "unknown"}, "NOT_FOUND"), ({"key": "unknown"}, "NOT_FOUND"),
                              ({"key": "../image.png"}, "INVALID_REQUEST"),
                              ({"key": "https://example.invalid/a"}, "INVALID_REQUEST"),
                              ({"path": "/etc/passwd"}, "INVALID_REQUEST"),
                              ({"size": "original"}, "INVALID_REQUEST"),
                              ({"dataRuntimeId": "unknown"}, "DEPENDENCY_MISSING")):
            with self.subTest(changes=changes), self.assertRaises(GbcError) as caught:
                self.media(**changes)
            self.assertEqual(caught.exception.code, code)

    def test_dataset_fence_before_after_and_no_full_hash(self):
        from kohakumoss_ui.contract import gbc_storage_files as files
        with patch.object(files, "snapshot", side_effect=AssertionError("no full hash")):
            self.media()
        actual = self.service._data
        def changed(*args):
            result = actual(*args)
            (self.copied / "extra").write_bytes(b"change")
            return result
        with patch.object(self.service, "_data", side_effect=changed), self.assertRaises(GbcError) as caught:
            self.media()
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
        with patch.object(self.service, "_data") as worker, self.assertRaises(GbcError):
            self.media()
        worker.assert_not_called()

    def test_dataset_hardlink_symlink_and_legacy_fence_rejected(self):
        target = self.copied / "shards/shard_00000/images.zip"
        for kind in ("hard", "symlink"):
            linked = self.case / ("link-" + kind)
            if kind == "hard":
                os.link(target, linked)
            else:
                target.rename(linked)
                target.symlink_to(linked)
            with self.assertRaises(GbcError):
                self.media()
            if kind == "hard":
                linked.unlink()
            else:
                target.unlink()
                linked.rename(target)
        self.service.db["datasets"]["dataset_test"].pop("_identities")
        with self.assertRaises(GbcError) as caught:
            self.media()
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")

    def artifact(self):
        staging = self.case / "frozen"
        images = staging / "images"
        images.mkdir(parents=True)
        image = images / "not-the-sample-name.png"
        image.write_bytes(png(256, 128))
        record = {"key": "sample_frozen", "image": str(image), "meta": {}}
        self.service.db["tasks"]["task_test"] = {"taskId": "task_test", "_staging": str(staging),
            "_records": [record], "_manifest": self.service._tree_manifest(staging)}
        self.service.db["jobs"]["job_test"] = {"taskId": "task_test"}
        self.service.db["artifacts"]["artifact_test"] = {"artifactId": "artifact_test", "jobId": "job_test",
            "_records": [record], "_imageRoot": str(images)}
        return image, {"artifactId": "artifact_test", "sampleId": "sample_frozen"}

    def test_artifact_uses_exact_frozen_mapping_and_hash(self):
        image, query = self.artifact()
        result = self.service.get("graphs/media", query)[1]["data"]["media"]
        self.assertEqual(result["sourceWidth"], 256)
        with self.assertRaises(GbcError):
            self.service.get("graphs/media", {**query, "sampleId": image.stem})
        image.write_bytes(png(128, 64))
        with self.assertRaises(GbcError) as caught:
            self.service.get("graphs/media", query)
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")

    def test_worker_input_codec_pixels_links_and_output_bounds(self):
        image = self.case / "image.png"
        def reject(data, code):
            image.write_bytes(data)
            with self.assertRaises(DataError) as caught:
                call(PYTHON, {"action": "frozen_media", "path": str(image),
                             "hash": "sha256:" + hashlib.sha256(data).hexdigest()})
            self.assertEqual(caught.exception.code, code)
        reject(b"<svg onload='alert(1)'/>", "invalid_image")
        from PIL import Image
        blob = io.BytesIO()
        Image.new("RGB", (16, 16)).save(blob, "BMP")
        reject(blob.getvalue(), "invalid_image")
        reject(png(8000, 5001), "limit_exceeded")
        with image.open("wb") as stream:
            stream.truncate((32 << 20) + 1)
        with self.assertRaises(DataError) as caught:
            call(PYTHON, {"action": "frozen_media", "path": str(image), "hash": "unused"})
        self.assertEqual(caught.exception.code, "limit_exceeded")
        image.write_bytes(png())
        for kind in ("hard", "symlink"):
            link = self.case / (kind + ".png")
            os.link(image, link) if kind == "hard" else link.symlink_to(image)
            with self.assertRaises(DataError) as caught:
                call(PYTHON, {"action": "frozen_media", "path": str(link), "hash": "unused"})
            self.assertEqual(caught.exception.code, "unsafe_path")
            link.unlink()
        # Exercise the actual output sink limit and deterministic downscaling.
        module_dir = Path(__file__).resolve().parents[1] / "src/kohakumoss_ui/contract"
        code = """import sys,io; sys.path.insert(0,sys.argv[1])
from PIL import Image
import gbc_data_media as m
buf=io.BytesIO(); Image.new('RGB',(2000,1000)).save(buf,'PNG')
assert m.render(buf.getvalue(),'thumb')['width']==384
assert m.render(buf.getvalue(),'detail')['width']==1600
m.MAX_OUTPUT=10
try: m.render(buf.getvalue(),'thumb')
except m.require.__globals__['Fault'] as e: assert e.code=='limit_exceeded'
else: raise AssertionError('output cap ignored')
"""
        result = subprocess.run([str(PYTHON), "-I", "-B", "-c", code, str(module_dir)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_http_real_media_auth_headers_and_curl(self):
        server = make_server(TrainerRoot(self.case / "trainer"), "127.0.0.1", 0, "media-secret")
        server.RequestHandlerClass.api._gbc = self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            path = "/api/gbc/v1/datasets/media?" + urlencode(self.query)
            for suffix, headers, expected in (("", {}, 403), ("&token=media-secret", {}, 403),
                    ("", {"Cookie": "km_token=media-secret"}, 403),
                    ("", {"X-UI-Token": "media-secret"}, 200)):
                con = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
                con.request("GET", path + suffix, headers=headers)
                response = con.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, expected, payload)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
                self.assertIn("sandbox", response.getheader("Content-Security-Policy"))
                con.close()
            con = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
            con.putrequest("GET", path)
            con.putheader("X-UI-Token", "media-secret")
            con.putheader("X-UI-Token", "media-secret")
            con.endheaders()
            response = con.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            con.close()
            result = subprocess.run(["curl", "--silent", "--show-error", "--fail", "--noproxy", "*",
                "-H", "X-UI-Token: media-secret", f"http://127.0.0.1:{server.server_port}" + path],
                capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout)["data"]["media"]["sourceHeight"], 128)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
