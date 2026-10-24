"""Synthetic-only preview limits, invalidation, concurrency and real HTTP tests."""

import hashlib
import io
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from unittest import mock
from urllib.parse import quote

from kohakumoss_ui.contract import grabber, grabber_preview as preview
from kohakumoss_ui.server.http import make_server
from test_grabber import GrabberCase

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "optional Pillow not installed")
class PreviewTests(GrabberCase):
    def picture(self, name="test.png", size=(900, 600), mode="RGB", color="red"):
        path = self.asg / "downloads" / name
        with Image.new(mode, size, color) as image:
            image.save(path)
        return path, "/files/" + name

    def failure(self, code, path, size="thumb"):
        with self.assertRaises(grabber.GrabberError) as caught:
            self.client.preview(path, size)
        self.assertEqual(caught.exception.status, code)

    def test_dimensions_no_upscale_original_unchanged_and_cache(self):
        path, raw = self.picture(size=(3072, 2048))
        original = path.read_bytes()
        with mock.patch.object(preview, "_render", wraps=preview._render) as render:
            thumb = self.client.preview(raw)
            self.assertIs(self.client.preview(raw), thumb)
            detail = self.client.preview(raw, "detail")
            self.assertEqual(render.call_count, 2)
        self.assertEqual((thumb.width, thumb.height), (384, 256))
        self.assertEqual((detail.width, detail.height), (1600, 1067))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.client.file(raw)[0], original)
        _, small = self.picture("small.png", (13, 17))
        self.assertEqual((self.client.preview(small).width, self.client.preview(small).height), (13, 17))

    def test_exif_transparency_fallback_and_animation_first_frame(self):
        path, raw = self.picture("rotated.jpg", (80, 40))
        with Image.open(path) as image:
            exif = image.getexif()
            exif[274] = 6
            image.save(path, exif=exif)
        result = self.client.preview(raw)
        self.assertEqual((result.width, result.height), (40, 80))
        _, raw = self.picture("alpha.png", (30, 20), "RGBA", (255, 0, 0, 0))
        for webp in (True, False):
            self.client._previews = preview.PreviewCache()
            with mock.patch("PIL.features.check", return_value=webp):
                result = self.client.preview(raw)
            self.assertEqual(result.mime, "image/webp" if webp else "image/png")
            with Image.open(io.BytesIO(result.data)) as image:
                self.assertEqual(image.convert("RGBA").getpixel((0, 0))[3], 0)
        first, second = Image.new("RGB", (20, 20), "red"), Image.new("RGB", (20, 20), "blue")
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.save(self.asg / "downloads/animated.gif", save_all=True, append_images=[second], duration=100)
        with Image.open(io.BytesIO(self.client.preview("/files/animated.gif").data)) as image:
            self.assertEqual(getattr(image, "n_frames", 1), 1)
            self.assertGreater(image.getpixel((0, 0))[0], 200)

    def test_mutation_identity_ctime_delete_and_link_replacement(self):
        path, raw = self.picture("ctime.bmp")
        before = path.stat()
        first = self.client.preview(raw)
        self.picture("ctime.bmp", color="blue")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertNotEqual(first.etag, self.client.preview(raw).etag)
        path.unlink()
        self.failure(404, raw)
        other, _ = self.picture("other.png")
        path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.client.preview(raw)

    def test_paths_magic_limits_dependency_and_errors_not_cached(self):
        path, raw = self.picture()
        for invalid in ("https://example.invalid/a.png", "/files/../test.png", "/files/%252e%252e/test.png",
                        "/files/%2e%2e/test.png", "/files/a\\b.png", "/files//test.png"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.client.preview(invalid)
        self.failure(400, raw, "large")
        self.failure(400, raw, "")
        self.failure(404, "/files/missing.png")
        (self.asg / "downloads/tags.txt").write_text("tag")
        self.failure(400, "/files/tags.txt")
        path.write_bytes(b"<svg>not raster</svg>")
        self.failure(422, raw)
        self.assertFalse(self.client._previews._cache)
        self.picture()
        with mock.patch.object(preview, "MAX_PIXELS", 10):
            self.failure(413, raw)
        with mock.patch.object(preview, "MAX_BYTES", 10):
            self.failure(413, raw)
        with mock.patch.object(preview, "_pillow", side_effect=preview.PreviewError(503, "Pillow unavailable")):
            self.failure(503, raw)
        self.assertFalse(self.client._previews._cache)
        with Image.new("RGB", (20, 20)) as image:
            image.save(path, format="JPEG")
        self.failure(422, raw)  # Correct raster but wrong extension/magic.

    def test_64mp_boundary_and_above_limit_before_decode(self):
        import struct
        import zlib

        self.assertEqual(preview.MAX_PIXELS, 64_000_000)
        self.assertEqual(preview.MAX_BYTES, 32 * 1024 * 1024)
        path = self.asg / "downloads/boundary.png"
        # A real 64MP raster, using one channel to bound regression-test memory.
        # Close the generated image before exercising the real decoder.
        with Image.linear_gradient("L") as gradient:
            with gradient.resize((8000, 8000)) as image:
                image.save(path)
        source = path.read_bytes()
        original_hash = hashlib.sha256(source).digest()
        measurements = []
        _, ImageOps, _ = preview._pillow()
        transpose = ImageOps.exif_transpose
        for size, edge in (("thumb", 384), ("detail", 1600)):
            def transpose_small(image, **kwargs):
                self.assertLessEqual(max(image.size), edge)
                return transpose(image, **kwargs)
            start = time.perf_counter()
            with mock.patch.object(ImageOps, "exif_transpose", side_effect=transpose_small):
                result = self.client.preview("/files/boundary.png", size)
            self.assertEqual((result.width, result.height), (edge, edge))
            measurements.append((size, result.width, result.height, len(result.data),
                                 round((time.perf_counter() - start) * 1000, 2)))
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), original_hash)
        print("64MP synthetic preview (variant,width,height,bytes,ms):", measurements)
        # Change only the synthetic PNG IHDR and its CRC. This is a real header
        # over the limit; rejection must happen before any pixel allocation.
        oversized = bytearray(source)
        oversized[16:20] = struct.pack(">I", 8001)
        oversized[29:33] = struct.pack(">I", zlib.crc32(oversized[12:29]))
        path.write_bytes(oversized)
        with mock.patch.object(Image.Image, "thumbnail") as thumbnail:
            with self.assertRaises(grabber.GrabberError) as caught:
                self.client.preview("/files/boundary.png")
            self.assertEqual(caught.exception.status, 413)
            self.assertIn("64 million", str(caught.exception))
            thumbnail.assert_not_called()

    def test_lru_count_and_byte_bounds(self):
        with mock.patch.object(preview, "MAX_ENTRIES", 2), mock.patch.object(preview, "MAX_CACHE_BYTES", 3000):
            for i in range(6):
                _, raw = self.picture(f"{i}.png", (30, 30), color=(i * 30, 0, 0))
                self.client.preview(raw)
                self.assertLessEqual(len(self.client._previews._cache), 2)
                self.assertLessEqual(self.client._previews._bytes, 3000)
            self.assertEqual(self.client._previews._bytes, sum(len(v.data) for v in self.client._previews._cache.values()))

    def test_parallel_decode_limit_and_duplicate_collapse(self):
        _, raw = self.picture()
        render = preview._render
        with mock.patch.object(preview, "_render", wraps=render) as wrapped:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.client.preview(raw), range(12)))
            self.assertEqual(wrapped.call_count, 1)
            self.assertTrue(all(result is results[0] for result in results))
        active, peak = 0, 0
        lock = threading.Lock()
        def slow(*args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.02)
                return render(*args)
            finally:
                with lock:
                    active -= 1
        paths = [self.picture(f"parallel{i}.png")[1] for i in range(8)]
        with mock.patch.object(preview, "_render", side_effect=slow):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(self.client.preview, paths))
        self.assertLessEqual(peak, 2)
        self.assertGreaterEqual(peak, 1)

    def test_change_during_decode_not_cached(self):
        path, raw = self.picture()
        render = preview._render
        def changing(*args):
            result = render(*args)
            path.write_bytes(b"changed")
            return result
        with mock.patch.object(preview, "_render", side_effect=changing):
            self.failure(409, raw)
        self.assertFalse(self.client._previews._cache)

    def test_http_private_304_auth_first_validation_and_original(self):
        path, raw = self.picture()
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        server = make_server(self.root, "127.0.0.1", 0, "preview-test")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(close)
        def request(route, token="preview-test", etag=None):
            conn = HTTPConnection(*server.server_address, timeout=5)
            headers = {"X-UI-Token": token}
            if etag:
                headers["If-None-Match"] = etag
            try:
                conn.request("GET", route, headers=headers)
                response = conn.getresponse()
                return response.status, response.headers, response.read()
            finally:
                conn.close()
        url = "/api/grabber/preview?path=" + quote(raw, safe="")
        code, headers, data = request(url)
        self.assertEqual(code, 200)
        self.assertEqual(headers.get_all("Cache-Control"), ["private, no-cache"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertEqual(request(url, etag=headers["ETag"])[::2], (304, b""))
        self.assertEqual(request(url, etag="W/" + headers["ETag"])[0], 304)
        self.assertEqual(request(url, token="wrong", etag=headers["ETag"])[0], 403)
        for suffix in ("&size=", "&size=bad", "&size=thumb&size=detail", "&path=" + quote(raw)):
            result = request(url + suffix, etag=headers["ETag"])
            self.assertEqual(result[0], 400)
            self.assertEqual(result[1]["Cache-Control"], "no-store")
        self.assertEqual(request("/api/grabber/preview?path=%25252e%25252e")[0], 400)
        code, original_headers, original_data = request("/api/grabber/file?path=" + quote(raw))
        self.assertEqual(code, 200)
        self.assertEqual(original_headers["Cache-Control"], "no-store")
        self.assertEqual(hashlib.sha256(original_data).hexdigest(), original)
        self.picture(color="blue")
        self.assertEqual(request(url, etag=headers["ETag"])[0], 200)
        path.unlink()
        self.assertEqual(request(url, etag=headers["ETag"])[0], 404)
