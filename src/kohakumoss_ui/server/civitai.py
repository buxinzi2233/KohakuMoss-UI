"""Read-only Civitai lookups: credentials, a cached snapshot, name matching.

Nothing here uploads. The API key is stored once (civitai.json, mode 0600) and
is never echoed by an endpoint, written to a log or embedded in an error
string; the UI only ever learns hasKey plus the masked tail.

Matching is name based and pure: normalize both sides and flag ambiguous hits.
Published state comes only from API snapshots; legacy manual marks are ignored.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import paths
from ..contract import runs
from ..store import jsonfile


FILE = "civitai.json"
BASE = "https://civitai.com/api/v1"
HOST_PREFIX = "https://civitai.com/"
USER_AGENT = "KohakuMoss-UI/0.1"
TIMEOUT = 15
PAGE_SIZE = 100
MAX_PAGES = 5
MAX_MODELS = 500
MAX_USERNAME = 100
MAX_KEY = 200
MAX_ERROR = 300
CACHE_TTL = 24 * 3600
EXPORT_SUFFIXES = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".pt2", ".zip", ".tar")

_LOCK = threading.Lock()


def _file():
    return paths.state_dir() / FILE


def _text(value, limit: int = 200) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _default() -> dict:
    return {"username": "", "apiKey": "", "syncedAt": 0.0, "models": []}


def _read() -> dict:
    raw = jsonfile.read_json(_file(), _default())
    record = _default()
    record["username"] = _text(raw.get("username"), MAX_USERNAME)
    record["apiKey"] = _text(raw.get("apiKey"), MAX_KEY)
    try:
        record["syncedAt"] = float(raw.get("syncedAt") or 0.0)
    except (TypeError, ValueError):
        record["syncedAt"] = 0.0
    models = raw.get("models")
    if isinstance(models, list):
        record["models"] = [item for item in models if isinstance(item, dict)][:MAX_MODELS]
    return record


def _write(record: dict) -> None:
    jsonfile.write_json(_file(), record)


def mask_key(key) -> str:
    """Last four characters only; never returns the usable secret."""
    text = _text(key, MAX_KEY)
    if not text:
        return ""
    if len(text) <= 4:
        return "****"
    return "****" + text[-4:]


def status() -> dict:
    """Public credential state: no key, no snapshot payload."""
    record = _read()
    return {
        "username": record["username"],
        "hasKey": bool(record["apiKey"]),
        "keyHint": mask_key(record["apiKey"]),
        "lastSync": record["syncedAt"],
    }


def save_credentials(username, api_key=None) -> dict:
    """Store the account name and (optionally) a key; never returns the key."""
    name = _text(username, MAX_USERNAME)
    if not name:
        raise ValueError("username is required")
    with _LOCK:
        record = _read()
        changed = record["username"] != name
        record["username"] = name
        if api_key is not None:
            record["apiKey"] = _text(api_key, MAX_KEY)
        elif changed:
            record["apiKey"] = ""
        if changed:
            record["models"] = []
            record["syncedAt"] = 0.0
        _write(record)
        return {
            "username": record["username"],
            "hasKey": bool(record["apiKey"]),
            "keyHint": mask_key(record["apiKey"]),
            "lastSync": record["syncedAt"],
        }


def delete_credentials() -> dict:
    with _LOCK:
        _write(_default())
    return {"username": "", "hasKey": False, "keyHint": "", "lastSync": 0.0}


def _strip_extension(text: str) -> str:
    lowered = text.lower()
    for suffix in EXPORT_SUFFIXES:
        if lowered.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _fold(text: str) -> str:
    folded = [char if char.isalnum() else "-" for char in text.lower()]
    result = "".join(folded)
    while "--" in result:
        result = result.replace("--", "-")
    return result.strip("-")


def normalize(name) -> str:
    """Civitai model key: lowercase, extension dropped, symbols folded to -."""
    return _fold(_strip_extension(_text(name, 300)))


def local_name(name) -> str:
    """Match key for a local run or export file: normalize plus the _lora trim."""
    text = _strip_extension(_text(name, 300))
    if text.lower().endswith("_lora"):
        text = text[:-5]
    return _fold(text)


def local_names(root) -> dict:
    """Match key -> run names that produced it (run directory, export stems)."""
    try:
        listed = runs.list_runs(root)
    except Exception:
        listed = []
    found: dict = {}
    for summary in listed:
        name = _text(summary.get("name"), 200)
        if not name:
            continue
        candidates = [name] + [str(item) for item in (summary.get("exports") or [])]
        for candidate in candidates:
            key = local_name(candidate)
            if not key:
                continue
            bucket = found.setdefault(key, [])
            if name not in bucket:
                bucket.append(name)
    return found


def match_local(local: dict, models) -> dict:
    """Pure name match: first hit wins, extra hits are flagged ambiguous."""
    index: dict = {}
    for model in models or []:
        if not isinstance(model, dict):
            continue
        key = _text(model.get("normalized"), 200) or normalize(model.get("name"))
        if not key:
            continue
        index.setdefault(key, []).append(model)
    found: dict = {}
    for key, sources in (local or {}).items():
        name = _text(key, 200)
        if not name:
            continue
        hits = index.get(name) or []
        if hits:
            hit = hits[0]
            found[name] = {
                "state": "published" if len(hits) == 1 and hit.get("id") is not None else "unknown",
                "modelId": hit.get("id"),
                "url": _text(hit.get("url"), 500),
                "name": _text(hit.get("name"), 200),
                "ambiguous": len(hits) > 1,
                "manual": False,
                "runs": list(sources or []),
            }
        else:
            found[name] = {
                "state": "draft",
                "modelId": None,
                "url": "",
                "name": "",
                "ambiguous": False,
                "manual": False,
                "runs": list(sources or []),
            }
    return found


def publish_map(root, models=None, checked=None) -> dict:
    """API evidence only. No snapshot means unknown, not an unpublished run."""
    record = _read() if models is None else {}
    snapshot = record.get("models") if models is None else models
    if checked is None:
        checked = models is not None or bool(record.get("username") and record.get("syncedAt"))
    if not isinstance(snapshot, list):
        snapshot = []
    matched = match_local(local_names(root), snapshot if checked else [])
    if not checked:
        for entry in matched.values():
            entry["state"] = "unknown"
    return matched


def published_count(marks) -> int:
    """A model matched by both a run and its export still counts only once."""
    return len({entry["modelId"] for entry in marks.values()
                if entry.get("state") == "published" and not entry.get("ambiguous")
                and entry.get("modelId") is not None})


def _guard_url(url: str) -> None:
    if not str(url).startswith(HOST_PREFIX):
        raise ValueError("refusing a non-civitai url")


def _get_json(url: str, api_key: str, opener=None) -> dict:
    _guard_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    if api_key:
        request.add_header("Authorization", "Bearer " + api_key)
    fetch = urllib.request.urlopen if opener is None else opener
    response = fetch(request, timeout=TIMEOUT)
    try:
        raw = response.read()
    finally:
        closer = getattr(response, "close", None)
        if callable(closer):
            closer()
    payload = json.loads(raw.decode("utf-8", "replace") or "{}")
    if not isinstance(payload, dict):
        raise ValueError("civitai returned an unexpected payload")
    return payload


def _model(row: dict) -> dict | None:
    model_id = row.get("id")
    name = _text(row.get("name"), 200)
    if model_id is None and not name:
        return None
    if not isinstance(model_id, (int, str)):
        model_id = str(model_id)
    return {
        "id": model_id,
        "name": name,
        "normalized": normalize(name),
        "url": "https://civitai.com/models/" + str(model_id),
        "type": _text(row.get("type"), 40),
        "createdAt": _text(row.get("createdAt"), 40),
    }


def _fetch_models(username: str, api_key: str, opener=None) -> list:
    models: list = []
    page = 1
    while page <= MAX_PAGES:
        url = (
            BASE
            + "/models?types=LORA&limit="
            + str(PAGE_SIZE)
            + "&username="
            + urllib.parse.quote(username, safe="")
            + "&page="
            + str(page)
        )
        payload = _get_json(url, api_key, opener)
        rows = payload.get("items")
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            model = _model(row)
            if model is not None:
                models.append(model)
            if len(models) >= MAX_MODELS:
                break
        if len(models) >= MAX_MODELS:
            break
        metadata = payload.get("metadata")
        next_page = metadata.get("nextPage") if isinstance(metadata, dict) else None
        if next_page is not None:
            try:
                page = int(next_page)
            except (TypeError, ValueError):
                page += 1
        elif len(rows) < PAGE_SIZE:
            break
        else:
            page += 1
    return models


def _error_text(exc, secret: str = "") -> str:
    if isinstance(exc, urllib.error.HTTPError):
        text = "civitai returned HTTP " + str(exc.code)
    elif isinstance(exc, TimeoutError):
        text = "civitai request timed out"
    elif isinstance(exc, urllib.error.URLError):
        text = "civitai request failed: " + str(getattr(exc, "reason", exc))
    else:
        text = type(exc).__name__ + ": " + str(exc)
    if secret:
        text = text.replace(secret, "***")
    return _text(text, MAX_ERROR)


def sync(root, username=None, refresh=False, opener=None, now=None) -> dict:
    """Fetch the account's public LoRA list; keep the old snapshot on failure."""
    moment = time.time() if now is None else float(now)
    record = _read()
    name = _text(username or record.get("username"), MAX_USERNAME)
    if not name:
        raise ValueError("username is required")
    same_account = record.get("username") == name
    snapshot = record.get("models") if same_account else []
    if not isinstance(snapshot, list):
        snapshot = []
    synced = float(record.get("syncedAt") or 0.0) if same_account else 0.0
    if not refresh and synced and (moment - synced) < CACHE_TTL:
        return {
            "username": name,
            "syncedAt": synced,
            "models": snapshot,
            "publish": publish_map(root, snapshot, checked=True),
            "cached": True,
            "error": "",
        }
    try:
        models = _fetch_models(name, record.get("apiKey") or "", opener)
    except Exception as exc:
        return {
            "username": name,
            "syncedAt": synced,
            "models": snapshot,
            "publish": publish_map(root, snapshot, checked=bool(synced)),
            "cached": bool(snapshot),
            "error": _error_text(exc, record.get("apiKey") or ""),
        }
    with _LOCK:
        fresh = _read()
        if fresh["username"] != record["username"] or fresh["apiKey"] != record["apiKey"]:
            return {
                "username": fresh["username"], "syncedAt": fresh["syncedAt"],
                "models": fresh["models"], "cached": bool(fresh["syncedAt"]),
                "publish": publish_map(root, fresh["models"], checked=bool(fresh["syncedAt"])),
                "error": "civitai account changed during query",
            }
        fresh["username"] = name
        fresh["models"] = models
        fresh["syncedAt"] = moment
        _write(fresh)
    return {
        "username": name,
        "syncedAt": moment,
        "models": models,
        "publish": publish_map(root, models),
        "cached": False,
        "error": "",
    }
