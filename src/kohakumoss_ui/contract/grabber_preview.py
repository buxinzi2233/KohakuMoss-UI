"""Bounded, memory-only raster derivatives; Pillow remains optional and lazy."""

import hashlib
import io
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

SIZES = {"thumb": 384, "detail": 1600}
MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 64_000_000
MAX_ENTRIES = 64
MAX_CACHE_BYTES = 32 * 1024 * 1024


class PreviewError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _pillow():
    try:
        from PIL import Image, ImageOps, features
    except ImportError:
        raise PreviewError(503, "Preview requires optional Pillow; open the original instead") from None
    return Image, ImageOps, features


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)


@dataclass(frozen=True)
class Preview:
    data: bytes
    mime: str
    etag: str
    width: int
    height: int


def _render(stream, mime, size):
    Image, ImageOps, features = _pillow()
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise PreviewError(413, "Preview source exceeds 32 MiB")
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP",
               "image/gif": "GIF", "image/bmp": "BMP"}
    try:
        with Image.open(io.BytesIO(raw), formats=[formats[mime]]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise PreviewError(413, "Preview source exceeds 64 million pixels")
            image.seek(0)  # Never encode an animation or advance to later frames.
            # Reduce before orientation/conversion to avoid full-size extra copies.
            image.thumbnail((SIZES[size], SIZES[size]), Image.Resampling.LANCZOS)
            with ImageOps.exif_transpose(image) as oriented:
                transparent = "A" in oriented.getbands() or "transparency" in oriented.info
                with oriented.convert("RGBA" if transparent else "RGB") as result:
                    # Do not forward EXIF, GPS, comments or other source metadata.
                    result.info.clear()
                    output = io.BytesIO()
                    if transparent and features.check("webp"):
                        result.save(output, format="WEBP", quality=82, method=3)
                        out_mime = "image/webp"
                    elif transparent:
                        result.save(output, format="PNG")
                        out_mime = "image/png"
                    else:
                        result.save(output, format="JPEG", quality=82)
                        out_mime = "image/jpeg"
                    data = output.getvalue()
                    return Preview(data, out_mime, '"' + hashlib.sha256(data).hexdigest() + '"',
                                   result.width, result.height)
    except PreviewError:
        raise
    except Image.DecompressionBombError:
        raise PreviewError(413, "Preview source exceeds safe pixel limit") from None
    except (OSError, ValueError, SyntaxError, KeyError):
        raise PreviewError(422, "Cannot decode raster preview; open the original instead") from None


class PreviewCache:
    def __init__(self):
        self._cache = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._decoders = threading.BoundedSemaphore(2)
        # Fixed stripes bound lock memory and collapse duplicate in-flight work.
        self._stripes = [threading.Lock() for _ in range(16)]

    def _get(self, key):
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def get(self, stream, mime, size, info):
        if size not in SIZES:
            raise PreviewError(400, "Preview size must be thumb or detail")
        if info.st_size > MAX_BYTES:
            raise PreviewError(413, "Preview source exceeds 32 MiB")
        key = (identity(info), mime, size)
        cached = self._get(key)
        if cached is not None:
            return cached
        with self._stripes[hash(key) % len(self._stripes)]:
            if identity(os.fstat(stream.fileno())) != key[0]:
                raise PreviewError(409, "Preview source changed; retry")
            cached = self._get(key)
            if cached is not None:
                return cached
            with self._decoders:
                # The descriptor can have changed while this request was waiting.
                if identity(os.fstat(stream.fileno())) != key[0]:
                    raise PreviewError(409, "Preview source changed; retry")
                value = _render(stream, mime, size)
                if identity(os.fstat(stream.fileno())) != key[0]:
                    raise PreviewError(409, "Preview source changed; retry")
            if len(value.data) <= MAX_CACHE_BYTES:
                with self._lock:
                    self._cache[key] = value
                    self._bytes += len(value.data)
                    while len(self._cache) > MAX_ENTRIES or self._bytes > MAX_CACHE_BYTES:
                        _, removed = self._cache.popitem(last=False)
                        self._bytes -= len(removed.data)
            return value
