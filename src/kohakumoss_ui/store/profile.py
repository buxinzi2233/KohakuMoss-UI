"""Profile: display name, selected runs, avatar and publish marks.

The Civitai credentials themselves live in server/civitai.py (one file, one
source of truth); this document only carries what the user owns: a name, up to six
pinned runs with notes, the uploaded avatar, and manual publish marks keyed by
the normalized model name.

Old favorite-only documents read as a single pin until the next user edit.
An explicit empty pins list stays empty; favorite mirrors the first pin for
older clients. All writes remain atomic under the existing profile lock.
"""

import os
import threading
import time
from pathlib import Path

from .. import paths
from . import jsonfile


FILE = "profile.json"
AVATAR_DIR = "avatars"
MAX_UPLOAD = 5 * 1024 * 1024
MAX_NAME = 120
MAX_NOTE = 2000
MAX_PINS = 6
MAX_PUBLISH = 500
PUBLISH_STATES = ("published", "draft", "unknown")

IMAGE_TYPES = {
    "image/png": ("png", ".png"),
    "image/jpeg": ("jpeg", ".jpg"),
    "image/webp": ("webp", ".webp"),
}

DEFAULT = {
    "displayName": "",
    "favorite": {"run": "", "note": ""},
    "publish": {},
}

_LOCK = threading.Lock()


def _file():
    return paths.state_dir() / FILE


def _avatar_dir() -> Path:
    folder = paths.state_dir() / AVATAR_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _text(value, limit: int = MAX_NAME) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _favorite(raw) -> dict:
    if not isinstance(raw, dict):
        return {"run": "", "note": ""}
    return {"run": _text(raw.get("run")), "note": _text(raw.get("note"), MAX_NOTE)}


def _publish(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    found: dict = {}
    rows = list(raw.items())[-MAX_PUBLISH:]
    for key, value in rows:
        name = _text(key, 200)
        if not name or not isinstance(value, dict):
            continue
        state = _text(value.get("state"), 40).lower()
        found[name] = {
            "state": state if state in PUBLISH_STATES else "unknown",
            "modelId": value.get("modelId"),
            "url": _text(value.get("url"), 500),
            "name": _text(value.get("name"), 200),
            "manual": bool(value.get("manual")),
            "checkedAt": float(value.get("checkedAt") or 0.0),
        }
    return found


def _pins(raw) -> list:
    if not isinstance(raw, list):
        raise ValueError("pins must be a list")
    if len(raw) > MAX_PINS:
        raise ValueError("at most six pinned runs")
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("pin must be an object")
        pin = _favorite(item)
        if not pin["run"] or pin["run"] in seen:
            raise ValueError("pinned runs must be nonempty and unique")
        seen.add(pin["run"])
        result.append(pin)
    return result


def _read_raw() -> dict:
    raw = jsonfile.read_json(_file(), DEFAULT)
    return {
        "displayName": _text(raw.get("displayName")),
        "favorite": _favorite(raw.get("favorite")),
        "pins": _pins(raw["pins"]) if "pins" in raw else (
            [_favorite(raw["favorite"])] if _favorite(raw.get("favorite"))["run"] else []
        ),
        "publish": _publish(raw.get("publish")),
    }


def _write_raw(data: dict) -> None:
    jsonfile.write_json(_file(), data)


def _image_path():
    folder = paths.state_dir() / AVATAR_DIR
    for suffix, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".webp", "image/webp")):
        candidate = folder / ("avatar" + suffix)
        if candidate.is_file():
            return candidate, mime
    return None


def avatar_file():
    """Return (path, mime) for the stored avatar, or None."""
    with _LOCK:
        return _image_path()


def _decorate(data: dict) -> dict:
    result = {
        "displayName": data["displayName"],
        "favorite": dict(data["favorite"]),
        "pins": [dict(item) for item in data["pins"]],
        "publish": {key: dict(value) for key, value in data["publish"].items()},
    }
    found = _image_path()
    if found is not None:
        try:
            stamp = int(found[0].stat().st_mtime)
        except OSError:
            stamp = 0
        result["avatarUrl"] = "/api/profile/avatar?v=" + str(stamp)
    else:
        result["avatarUrl"] = ""
    return result


def read() -> dict:
    with _LOCK:
        return _decorate(_read_raw())


def save(patch: dict) -> dict:
    """Merge a partial profile record and persist it atomically."""
    if not isinstance(patch, dict):
        raise ValueError("profile patch must be an object")
    with _LOCK:
        data = _read_raw()
        if "displayName" in patch:
            data["displayName"] = _text(patch.get("displayName"))
        if isinstance(patch.get("favorite"), dict):
            favorite = dict(data["favorite"])
            given = patch["favorite"]
            if "run" in given:
                favorite["run"] = _text(given.get("run"))
            if "note" in given:
                favorite["note"] = _text(given.get("note"), MAX_NOTE)
            data["favorite"] = favorite
            data["pins"] = [dict(favorite)] if favorite["run"] else []
        if "pins" in patch:
            data["pins"] = _pins(patch["pins"])
            data["favorite"] = dict(data["pins"][0]) if data["pins"] else {"run": "", "note": ""}
        _write_raw(data)
        return _decorate(data)


def publish() -> dict:
    """Manual publish marks keyed by normalized model name."""
    with _LOCK:
        return _read_raw()["publish"]


def set_publish(key: str, patch: dict) -> dict:
    """Store one publish mark; the caller supplies the normalized key."""
    name = _text(key, 200)
    if not name:
        raise ValueError("publish key is required")
    if not isinstance(patch, dict):
        raise ValueError("publish patch must be an object")
    with _LOCK:
        data = _read_raw()
        record = dict(data["publish"].get(name) or {})
        for field in ("state", "modelId", "url", "name", "manual"):
            if field not in patch:
                continue
            value = patch[field]
            if field == "state":
                text = _text(value, 40).lower()
                if text not in PUBLISH_STATES:
                    raise ValueError("unknown publish state: " + text)
                record["state"] = text
            elif field == "manual":
                record["manual"] = bool(value)
            elif field == "modelId":
                record["modelId"] = value
            elif field == "url":
                record["url"] = _text(value, 500)
            else:
                record["name"] = _text(value, 200)
        record["checkedAt"] = time.time()
        marks = dict(data["publish"])
        marks[name] = record
        if len(marks) > MAX_PUBLISH:
            ordered = sorted(marks.items(), key=lambda pair: float(pair[1].get("checkedAt") or 0.0))
            for stale, _value in ordered[: len(marks) - MAX_PUBLISH]:
                marks.pop(stale, None)
        data["publish"] = marks
        _write_raw(data)
        return dict(record)


def _magic_ok(data: bytes, kind: str) -> bool:
    if kind == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if kind == "webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def save_avatar(data: bytes, mime: str) -> dict:
    """Store one uploaded avatar; replaces any previous file."""
    if not data:
        raise ValueError("empty image body")
    if len(data) > MAX_UPLOAD:
        raise ValueError("image is larger than 5 MB")
    kind = IMAGE_TYPES.get((mime or "").split(";")[0].strip().lower())
    if kind is None:
        raise ValueError("unsupported image type: use png, jpeg or webp")
    label, suffix = kind
    if not _magic_ok(data, label):
        raise ValueError("image data does not match its content type")
    with _LOCK:
        folder = _avatar_dir()
        for old in folder.glob("avatar.*"):
            try:
                old.unlink()
            except OSError:
                pass
        target = folder / ("avatar" + suffix)
        tmp = folder / ("avatar" + suffix + ".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        return _decorate(_read_raw())


def remove_avatar() -> dict:
    with _LOCK:
        folder = paths.state_dir() / AVATAR_DIR
        if folder.is_dir():
            for old in folder.glob("avatar.*"):
                try:
                    old.unlink()
                except OSError:
                    pass
    return {"ok": True}
