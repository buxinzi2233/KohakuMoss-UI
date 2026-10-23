"""Appearance state: theme, glass surfaces, wallpaper settings and palette.

Everything the UI needs to look right is persisted in one JSON file under the
UI state directory, next to a single optional background image.
"""

import io
import json
import os
import threading
from pathlib import Path

from .. import paths


MAX_UPLOAD = 25 * 1024 * 1024
MAX_URL = 1_000_000

THEMES = ("kohaku", "soul", "teletype")
MODES = ("light", "carbon")
# "auto" follows the browser locale; the UI resolves it to a concrete language.
LANGUAGES = ("auto", "en", "zh")
COLOR_KEYS = ("canvas", "panel", "text", "border", "accent")

IMAGE_TYPES = {
    "image/png": ("png", ".png"),
    "image/jpeg": ("jpeg", ".jpg"),
    "image/webp": ("webp", ".webp"),
}

DEFAULT = {
    "theme": "teletype",
    "mode": "light",
    "lang": "auto",
    "glass": False,
    "surfaceOpacity": 0.82,
    "background": {"enabled": False, "url": "", "blur": 5, "mask": 0.5},
    # Per-theme colour overrides: empty string means "use the theme value".
    "colors": {name: {key: "" for key in COLOR_KEYS} for name in THEMES},
}

_LOCK = threading.Lock()

try:  # optional: Pillow is present in the trainer venv, never required
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Image = None


def _file() -> Path:
    return paths.state_dir() / "appearance.json"


def _background_dir() -> Path:
    folder = paths.state_dir() / "backgrounds"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _clamp(value, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _flag(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _color(value, default: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        body = text[1:]
        if all(char in "0123456789abcdefABCDEF" for char in body):
            return text.lower()
    return default


def _optional_color(value) -> str:
    """Colour override: a hex value, or "" to fall back to the theme."""
    return _color(value, "")


LEGACY_COLORS = {
    "canvas": "paper",
    "panel": "panel",
    "text": "ink",
    "border": "border",
    "accent": "accent",
}


def _text(value, default: str) -> str:
    if not isinstance(value, str):
        return default
    return value[:MAX_URL]


def _normalize(raw: dict) -> dict:
    """Build a complete appearance record from a possibly partial dict."""
    background = raw.get("background") if isinstance(raw.get("background"), dict) else {}
    theme = raw.get("theme")
    mode = raw.get("mode")

    colors = {name: {key: "" for key in COLOR_KEYS} for name in THEMES}
    stored = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    for name in THEMES:
        given = stored.get(name) if isinstance(stored.get(name), dict) else {}
        for key in COLOR_KEYS:
            colors[name][key] = _optional_color(given.get(key))

    # Migrate the old fourth theme ("custom") into teletype overrides.
    legacy = raw.get("custom") if isinstance(raw.get("custom"), dict) else {}
    if theme == "custom":
        theme = DEFAULT["theme"]
        for key, old in LEGACY_COLORS.items():
            if not colors[theme][key]:
                colors[theme][key] = _color(legacy.get(old), "")

    lang = raw.get("lang")

    return {
        "theme": theme if theme in THEMES else DEFAULT["theme"],
        "mode": mode if mode in MODES else DEFAULT["mode"],
        "lang": lang if lang in LANGUAGES else DEFAULT["lang"],
        "glass": _flag(raw.get("glass"), DEFAULT["glass"]),
        "surfaceOpacity": _clamp(raw.get("surfaceOpacity"), 0.0, 1.0, DEFAULT["surfaceOpacity"]),
        "background": {
            "enabled": _flag(background.get("enabled"), DEFAULT["background"]["enabled"]),
            "url": _text(background.get("url"), DEFAULT["background"]["url"]),
            "blur": _clamp(background.get("blur"), 0.0, 30.0, DEFAULT["background"]["blur"]),
            "mask": _clamp(background.get("mask"), 0.0, 1.0, DEFAULT["background"]["mask"]),
        },
        "colors": colors,
    }


def _read_raw() -> dict:
    try:
        raw = json.loads(_file().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return _normalize(raw if isinstance(raw, dict) else {})


def _write_raw(data: dict) -> None:
    target = _file()
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(tmp, target)


def _image_path():
    folder = paths.state_dir() / "backgrounds"
    for suffix, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".webp", "image/webp")):
        candidate = folder / ("custom" + suffix)
        if candidate.is_file():
            return candidate, mime
    return None


def background_file():
    """Return (path, mime) for the stored background, or None."""
    with _LOCK:
        return _image_path()


def _decorate(data: dict) -> dict:
    result = json.loads(json.dumps(data))
    stored = result["background"].get("url") or ""
    if stored.startswith(("http://", "https://", "data:image/")):
        result["backgroundUrl"] = stored
        return result
    found = _image_path()
    if found is not None:
        try:
            stamp = int(found[0].stat().st_mtime)
        except OSError:
            stamp = 0
        result["backgroundUrl"] = f"/api/appearance/background?v={stamp}"
    else:
        result["backgroundUrl"] = ""
    return result


def read() -> dict:
    with _LOCK:
        return _decorate(_read_raw())


def save(patch: dict) -> dict:
    """Merge a partial appearance record and persist it atomically."""
    if not isinstance(patch, dict):
        raise ValueError("appearance patch must be an object")
    with _LOCK:
        current = _read_raw()
        merged = dict(current)
        for key in ("theme", "mode", "glass", "surfaceOpacity"):
            if key in patch:
                merged[key] = patch[key]
        # An unknown language is ignored rather than reset: a stale client should
        # not overwrite the choice the user already made.
        if patch.get("lang") in LANGUAGES:
            merged["lang"] = patch["lang"]
        for key in ("background",):
            if isinstance(patch.get(key), dict):
                merged[key] = {**current[key], **patch[key]}
        if isinstance(patch.get("colors"), dict):
            colors = {name: dict(values) for name, values in current["colors"].items()}
            for name, values in patch["colors"].items():
                if name in colors and isinstance(values, dict):
                    colors[name] = {**colors[name], **values}
            merged["colors"] = colors
        data = _normalize(merged)
        _write_raw(data)
        return _decorate(data)


def _magic_ok(data: bytes, kind: str) -> bool:
    if kind == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if kind == "webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def save_background(data: bytes, mime: str) -> dict:
    """Store one uploaded image; replaces any previous file."""
    if not data:
        raise ValueError("empty image body")
    if len(data) > MAX_UPLOAD:
        raise ValueError("image is larger than 25 MB")
    kind = IMAGE_TYPES.get((mime or "").split(";")[0].strip().lower())
    if kind is None:
        raise ValueError("unsupported image type: use png, jpeg or webp")
    label, suffix = kind
    if not _magic_ok(data, label):
        raise ValueError("image data does not match its content type")
    if Image is not None:
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception as exc:  # noqa: BLE001 - any decoder failure is a bad image
            raise ValueError(f"image failed verification: {exc}") from exc
    with _LOCK:
        folder = _background_dir()
        for old in folder.glob("custom.*"):
            try:
                old.unlink()
            except OSError:
                pass
        target = folder / ("custom" + suffix)
        tmp = folder / ("custom" + suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        state = _read_raw()
        state["background"]["url"] = "/api/appearance/background"
        state["background"]["enabled"] = True
        _write_raw(state)
        return _decorate(state)


def remove_background() -> dict:
    with _LOCK:
        folder = paths.state_dir() / "backgrounds"
        if folder.is_dir():
            for old in folder.glob("custom.*"):
                try:
                    old.unlink()
                except OSError:
                    pass
        state = _read_raw()
        state["background"]["url"] = ""
        state["background"]["enabled"] = False
        _write_raw(state)
    return {"ok": True}
