"""ArtistSourceGrabber subprocess/HTTP contract, never its UI or Python imports.

Only the child started by this instance is contacted. No port scanning, external
service adoption, proxy inheritance, or automatic task/model/browser startup.
"""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .. import paths
from ..store.jsonfile import read_json, write_json
from .grabber_models import scan_models
from .grabber_preview import PreviewCache, PreviewError, SIZES


SOURCES = frozenset({"danbooru", "twitter", "openverse", "pixiv", "gelbooru",
                     "safebooru", "konachan", "yandere"})
SECRET_FIELDS = frozenset({"api_key", "auth_token", "auth_key", "ct0", "access_token",
                           "phpsessid", "password_hash", "llm_api_key"})
FIELDS = {
    "danbooru": {"login", "api_key", "proxy"},
    "twitter": {"auth_token", "ct0", "x_cookies_file", "x_cookies_from_browser",
                "x_browser_profile", "x_cookie_mode", "x_mode", "proxy"},
    "pixiv": {"access_token", "phpsessid", "user_id", "pixiv_cookie_mode", "proxy"},
    "gelbooru": {"user_id", "api_key", "proxy"},
    "safebooru": {"user_id", "api_key", "proxy"},
    "konachan": {"login", "password_hash", "proxy"},
    "yandere": {"login", "password_hash", "proxy"},
    "openverse": {"openverse_license", "proxy"},
    "tagger:openai_compatible": {"endpoint", "model", "api_key", "prompt", "timeout",
                                  "max_tokens", "temperature"},
    "tagger:local_onnx": {"model_path", "tags_path", "selected_tags_path",
                           "general_threshold", "threshold", "character_threshold",
                           "input_size", "providers", "categories"},
}
# Only these task-wide values may be inherited by multiple sources. In
# particular, even non-secret source options (login/proxy/cookie mode) stay local.
SHARED_FIELDS = frozenset({
    "count", "start_page", "end_page", "range_start", "range_end",
    "canonical_artist", "canonical_artist_id", "tag_format", "tag_merge_mode",
    "include_artist", "include_meta", "skip_video", "background_enabled", "background_color",
    "images_only", "tagger_id", "tagger_type", "tagger", "tagger_config",
})
SOURCE_QUERY_FIELDS = frozenset({"artist", "query_type", "rating"})
SOURCE_EXTRA_FIELDS = {"twitter": {"x_user_id", "x_handle"}}
REDACT_FIELDS = SECRET_FIELDS | {"authorization", "cookie", "cookies"}
_STORE_LOCK = threading.RLock()
MAX_RESPONSE = 16 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
START_TIMEOUT = 15.0


class GrabberError(Exception):
    """Safe structured failure; payload must contain no credentials."""

    def __init__(self, status, message, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {"ok": False, "error": message}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _images_only(value=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
    raise ValueError("images_only must be a boolean")


def _source(source, tagger=True):
    allowed = FIELDS if tagger else SOURCES
    if not isinstance(source, str) or source not in allowed:
        raise ValueError("unsupported grabber source")
    return source


def _store_path():
    return paths.state_dir() / "grabber-credentials.json"


def _saved(source):
    record = read_json(_store_path(), {}).get(source, {})
    return {k: v for k, v in record.items() if k in FIELDS[source]} if isinstance(record, dict) else {}


def _safe_value(key, value):
    if isinstance(value, (dict, list)) and not (key in {"providers", "categories"}
                                              and isinstance(value, list)
                                              and all(isinstance(v, str) for v in value)):
        raise ValueError("credential field has invalid type")
    if value is not None and not isinstance(value, (str, int, float, bool, list)):
        raise ValueError("credential field has invalid type")
    if key in SECRET_FIELDS and not isinstance(value, str):
        raise ValueError("secret fields must be strings")
    if key in {"endpoint", "proxy"} and value:
        if not isinstance(value, str):
            raise ValueError("endpoint/proxy must be a URL string")
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("endpoint/proxy must not contain credentials, query or fragment")
    return value


def credentials(source, values=None, clear=None):
    """Private atomic storage; blank secrets retain the old value, clear wins."""
    source = _source(source)
    if clear is None:
        clear = []
    with _STORE_LOCK:
        saved = _saved(source)
        if values is not None:
            if not isinstance(values, dict) or not isinstance(clear, list):
                raise ValueError("values must be an object and clear must be an array")
            if any(k not in FIELDS[source] for k in values):
                raise ValueError("unsupported credential field")
            if any(not isinstance(k, str) or k not in FIELDS[source] for k in (clear or [])):
                raise ValueError("unsupported clear field")
            for key, value in values.items():
                value = _safe_value(key, value)
                if key in SECRET_FIELDS and not value.strip():
                    continue
                saved[key] = value
            for key in clear or []:
                saved.pop(key, None)
            records = read_json(_store_path(), {})
            records[source] = saved
            write_json(_store_path(), records)
        return {"source": source,
                "values": {k: v for k, v in saved.items() if k not in SECRET_FIELDS},
                "configured": sorted(k for k, v in saved.items() if k in SECRET_FIELDS and v)}


@contextmanager
def _open_media_posix(root, rel):
    """Directory-relative, no-follow open; also stops a concurrent symlink swap."""
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parts = rel.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("media must be a regular file")
            yield stream, info
    finally:
        if fd is not None:
            os.close(fd)


@contextmanager
def _open_media_windows(root, rel):
    """Windows lacks dir_fd/O_NOFOLLOW: re-check every segment, then open by path."""
    parts = rel.split("/")
    current = root
    for part in parts[:-1]:
        current = current / part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("media symlinks are not supported")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid media path")
    target = current / parts[-1]
    info = os.lstat(target)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("media symlinks are not supported")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("media must be a regular file")
    with open(target, "rb") as stream:
        # Re-check the opened handle: no path swap may slip in between lstat and open.
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("media must be a regular file")
        yield stream, info


class Grabber:
    def __init__(self, trainer_root):
        default = trainer_root.root.parent / "ArtistSourceGrabber"
        self.root = Path(os.environ.get("KOHAKUMOSS_GRABBER_ROOT") or default).expanduser().resolve()
        self.download_dir = (self.root / "downloads").resolve()
        self._lock = threading.RLock()
        self._task_lock = threading.Lock()
        self._secret_lock = threading.Lock()
        self._secrets = set()
        self._process = None
        self._stdout = None
        self._stdout_path = None
        self._port = None
        self._error = ""
        self._closed = False
        self._opener = build_opener(ProxyHandler({}), NoRedirect())
        self._previews = PreviewCache()

    def _remember(self, value):
        with self._secret_lock:
            def walk(obj):
                if isinstance(obj, dict):
                    for key, val in obj.items():
                        if key.lower() in REDACT_FIELDS and isinstance(val, str) and val:
                            self._secrets.add(val)
                        else:
                            walk(val)
                elif isinstance(obj, list):
                    for val in obj:
                        walk(val)
            walk(value)

    def _redact(self, value):
        with _STORE_LOCK:
            self._remember(read_json(_store_path(), {}))
        with self._secret_lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        def clean(obj):
            if isinstance(obj, dict):
                return {k: clean(v) for k, v in obj.items()
                        if k.lower() not in SECRET_FIELDS and k.lower() not in
                        {"config", "cfg", "request", "source_configs", "tagger_config",
                         "authorization", "cookie", "cookies"}}
            if isinstance(obj, list):
                return [clean(v) for v in obj]
            if isinstance(obj, str):
                for secret in secrets:
                    obj = obj.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
                obj = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", obj)
                obj = re.sub(r"(?i)((?:api[_-]?key|auth_token|access_token|phpsessid|ct0|password_hash|authorization|cookie)\s*[=:]\s*)[^\s,;&]+", r"\1[redacted]", obj)
            return obj
        return clean(value)

    def _request(self, path, body=None, timeout=30):
        # The port is only learned from this child's successful bind announcement.
        with self._lock:
            process, port = self._process, self._port
            if process is None or process.poll() is not None or port is None:
                raise GrabberError(503, "Grabber is not running; start its service explicitly")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(f"http://127.0.0.1:{port}{path}", data=data,
                          headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as exc:
            with exc:
                raw = exc.read(MAX_RESPONSE + 1)
            try:
                payload = json.loads(raw) if len(raw) <= MAX_RESPONSE else {}
            except (ValueError, UnicodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            status = exc.code if exc.code in {400, 401, 403, 404, 409, 422, 429} else 502
            safe = self._redact(payload)
            # Never relay HTML/redirect targets or arbitrary exception reprs.
            if not safe.get("error"):
                safe = {"ok": False, "error": f"Grabber HTTP error ({exc.code})"}
            raise GrabberError(status, "Grabber request failed", safe) from None
        except (OSError, URLError, TimeoutError):
            raise GrabberError(503, "Grabber is unreachable or timed out") from None
        if len(raw) > MAX_RESPONSE:
            raise GrabberError(502, "Grabber response exceeds 16 MiB")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise GrabberError(502, "Grabber returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise GrabberError(502, "Grabber returned a non-object response")
        return self._redact(payload)

    def status(self):
        with self._lock:
            available = (self.root / "app.py").is_file()
            alive = self._process is not None and self._process.poll() is None
            running = False
            error = self._error
            if alive and self._port:
                try:
                    payload = self._request("/api/progress?logs=2147483647&items=2147483647", timeout=2)
                    running = isinstance(payload.get("status"), str)
                    if not running:
                        error = "Grabber readiness response is invalid"
                except GrabberError:
                    error = "Managed Grabber is not responding"
            elif not available:
                error = "ArtistSourceGrabber checkout is missing (app.py not found)"
            elif not error:
                error = "Grabber service has not been started by this UI"
            return {"available": available, "running": running, "managed": alive,
                    "root": str(self.root), "download_dir": str(self.download_dir),
                    "error": "" if running else error,
                    "pid": self._process.pid if alive else None,
                    "port": self._port if alive else None}

    def start_service(self):
        with self._lock:
            if self._closed:
                raise GrabberError(503, "UI service is closing")
            if self._process is not None and self._process.poll() is None:
                return self.status()
            if not (self.root / "app.py").is_file():
                return self.status()
            self._close_stdout()
            candidate = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python = os.environ.get("KOHAKUMOSS_GRABBER_PYTHON") or (str(candidate) if candidate.is_file() else sys.executable)
            self._port = None
            self._error = ""
            # An unlinked file, not a PIPE: UI exit cannot cause child BrokenPipe.
            if os.name == "nt":
                # Windows TemporaryFile is O_TEMPORARY and cannot be reopened by
                # path, so poll a separate read handle on a named temp file.
                handle, self._stdout_path = tempfile.mkstemp(
                    prefix="kohakumoss-grabber-", suffix=".log")
                self._stdout = os.fdopen(handle, "w+b")
            else:
                self._stdout = tempfile.TemporaryFile(mode="w+b")
            try:
                self._process = subprocess.Popen(
                    [python, str(self.root / "app.py"), "--no-browser"], cwd=self.root,
                    env={**os.environ, "PYTHONUNBUFFERED": "1",
                         "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    stdin=subprocess.DEVNULL,
                    stdout=self._stdout, stderr=subprocess.STDOUT, start_new_session=True,
                )
                deadline = time.monotonic() + START_TIMEOUT
                offset = 0
                tail = b""
                while time.monotonic() < deadline and self._process.poll() is None:
                    chunk = self._read_new_output(offset)
                    offset += len(chunk)
                    tail = (tail + chunk)[-16384:]
                    banner = "  \u754c\u9762\u5730\u5740\uff1a".encode("utf-8")
                    match = re.search(re.escape(banner) + rb"http://127\.0\.0\.1:(871[0-9])\r?\n", tail)
                    if match is None:
                        # A non-UTF-8 console page cannot encode the Chinese prefix,
                        # so accept an indented line whose bytes before the URL are
                        # all non-ASCII. A stray URL inside an error line never wins.
                        match = re.search(
                            rb"(?m)^[ \t]+[^\x20-\x7e]*http://127\.0\.0\.1:(871[0-9])\r?\n", tail)
                    if match:
                        self._port = int(match.group(1))
                        if self.status()["running"]:
                            return self.status()
                    time.sleep(0.05)
                self._error = "Grabber startup failed or timed out; check its Python dependencies and ports 8710-8719"
            except (OSError, ValueError):
                self._error = "Cannot launch Grabber; check checkout and KOHAKUMOSS_GRABBER_PYTHON"
            self._cleanup_failed_start()
            return self.status()

    def _read_new_output(self, offset):
        """Log bytes appended after `offset`, without moving the child's write offset."""
        if self._stdout_path is None:
            # pread leaves the shared child file offset untouched.
            return os.pread(self._stdout.fileno(), 8192, offset)
        # A fresh read handle per poll also leaves the child's offset untouched.
        with open(self._stdout_path, "rb") as stream:
            stream.seek(offset)
            return stream.read(8192)

    def _close_stdout(self):
        """Drop the child log handle; on Windows also remove its temp file."""
        if self._stdout is not None:
            self._stdout.close()
            self._stdout = None
        path, self._stdout_path = self._stdout_path, None
        if path is not None:
            # Windows refuses to unlink while the child still holds the file, which
            # simply defers the removal to the next cleanup attempt.
            try:
                os.unlink(path)
            except OSError:
                pass

    def _cleanup_failed_start(self):
        # Only this exact Popen handle, and only before a task could be submitted.
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
        self._port = None
        self._close_stdout()

    def close(self):
        """Detach without stopping tasks; no guessed PID adoption on UI restart."""
        with self._lock:
            self._closed = True
            self._close_stdout()
            # The child retains its output fd and own session. Deliberately do not
            # call shutdown/terminate: upstream shutdown abandons worker threads.

    def _merge(self, body, source_required=True, allow_multi=True):
        self._remember(body)
        result = dict(body)
        result["images_only"] = _images_only(result.get("images_only", False))
        if result["images_only"]:
            # Drop stale selectors/configs before validating or loading saved AI settings.
            for key in list(result):
                if key in {"tagger", "tagger_type", "tagger_config"} or key.startswith(("llm_", "onnx_")):
                    result.pop(key)
            result["tagger_id"] = "none"
        if "source" in result:
            _source(result["source"], tagger=False)
        multi = "sources" in result or "source_configs" in result
        if multi:
            if not allow_multi or not source_required:
                raise ValueError("this endpoint requires a single source")
            selected = result.get("sources")
            if not isinstance(selected, list) or not 1 <= len(selected) <= 8:
                raise GrabberError(400, "sources must contain between 1 and 8 source IDs")
            selected = list(dict.fromkeys(_source(s, tagger=False) for s in selected))
            configs = result.get("source_configs", {})
            if not isinstance(configs, dict) or any(s not in selected for s in configs):
                raise GrabberError(400, "source_configs must contain only selected source IDs")
            if set(result) - SHARED_FIELDS - {"sources", "source_configs"}:
                raise GrabberError(400, "Multi-source settings and credentials must be nested in source_configs")
            for key in SHARED_FIELDS - {"tagger_config"}:
                if key in result:
                    _safe_value(key, result[key])
            result["sources"] = selected
            result["source_configs"] = {}
            for source in selected:
                config = configs.get(source, {})
                allowed = FIELDS[source] | SOURCE_QUERY_FIELDS | SOURCE_EXTRA_FIELDS.get(source, set())
                if not isinstance(config, dict) or set(config) - allowed:
                    raise GrabberError(400, "Invalid source-specific fields; tagger and images_only are task-wide")
                for key, value in config.items():
                    _safe_value(key, value)
                with _STORE_LOCK:
                    saved = _saved(source)
                result["source_configs"][source] = {**saved, **{
                    k: v for k, v in config.items() if v not in (None, "")}}
        elif source_required:
            source = _source(body.get("source", "danbooru"), tagger=False)
            with _STORE_LOCK:
                saved = _saved(source)
            for key in FIELDS[source]:
                if key in result:
                    _safe_value(key, result[key])
            for key in SECRET_FIELDS:
                if key in result and key not in FIELDS[source]:
                    raise ValueError("credential field does not belong to this source")
            result = {**saved, **{k: v for k, v in result.items() if v not in (None, "")}}
            result["source"] = source
        tagger = result.get("tagger_type") or result.get("tagger_id") or result.get("tagger") or "none"
        if not isinstance(tagger, str) or tagger not in {"none", "openai_compatible", "local_onnx"}:
            raise ValueError("use tagger_id none/openai_compatible/local_onnx with tagger_config")
        if tagger != "none":
            config = result.get("tagger_config", {})
            if not isinstance(config, dict):
                raise ValueError("tagger_config must be an object")
            source = "tagger:" + tagger
            if any(k not in FIELDS[source] for k in config):
                raise ValueError("unsupported tagger field")
            for k, v in config.items():
                _safe_value(k, v)
            with _STORE_LOCK:
                saved = _saved(source)
            result["tagger_config"] = {**saved, **{k: v for k, v in config.items() if v not in (None, "")}}
        self._remember(result)
        return result

    def _require_capabilities(self, *names):
        metadata = self._request("/api/sources")
        capabilities = metadata.get("capabilities", {})
        if not isinstance(capabilities, dict) or any(capabilities.get(name) is not True for name in names):
            raise GrabberError(409, "This operation is not supported by the running ASG; update and restart ArtistSourceGrabber")
        return metadata

    def _check_multi(self, merged, metadata):
        capabilities = metadata.get("capabilities", {})
        if isinstance(capabilities, dict) and "multi_source" in capabilities and capabilities["multi_source"] is not True:
            raise GrabberError(409, "Multi-source is not supported; update and restart ArtistSourceGrabber")
        sources = metadata.get("sources")
        if not isinstance(sources, list):
            raise GrabberError(502, "ASG returned invalid source metadata")
        available = {s["id"]: s for s in sources if isinstance(s, dict) and isinstance(s.get("id"), str)}
        for source in merged["sources"]:
            if source not in available:
                raise GrabberError(409, "Selected source is not available in the running ASG: " + source)
            config = merged["source_configs"][source]
            artist = config.get("artist")
            if not isinstance(artist, str) or not artist.strip():
                raise GrabberError(400, "Each selected source needs an explicit artist/query mapping: " + source)
            meta = available[source]
            query_types, auth_fields = meta.get("query_types", []), meta.get("auth_fields", [])
            if not isinstance(query_types, list) or not isinstance(auth_fields, list):
                raise GrabberError(502, "ASG returned invalid source capabilities")
            query_type = config.get("query_type", "artist")
            if not isinstance(query_type, str) or ("query_types" in meta and query_type not in query_types):
                raise GrabberError(400, "Selected source does not support this query type: " + source)
            # Managed browser sessions cannot be inferred from saved fields. ASG
            # performs their real auth preflight; never guess an identity or skip.
            if meta.get("needs_auth") is True:
                required = [field["id"] for field in auth_fields
                            if isinstance(field, dict) and isinstance(field.get("id"), str)
                            and field.get("required") is True]
                if any(not config.get(key) for key in required):
                    raise GrabberError(400, "Selected source requires authentication: " + source)

    def models_scan(self, body):
        if set(body) - {"directory"}:
            raise GrabberError(400, "Model scan accepts only directory")
        directory = body.get("directory")
        if "directory" in body and (not isinstance(directory, str) or not directory.strip()):
            raise GrabberError(400, "Model scan directory must be a non-empty string")
        if directory is None:
            with _STORE_LOCK:
                saved = _saved("tagger:local_onnx").get("model_path")
            parent = Path(saved).expanduser().parent if isinstance(saved, str) and saved else None
            directory = parent if parent is not None and parent.is_dir() else self.root / "models"
        try:
            return scan_models(directory)
        except (FileNotFoundError, NotADirectoryError):
            raise GrabberError(404, "Model scan directory does not exist or is not a directory") from None
        except PermissionError:
            raise GrabberError(403, "Model scan directory is not readable") from None
        except (ValueError, RuntimeError):
            raise GrabberError(400, "Model scan directory is invalid or a symlink") from None
        except OSError:
            raise GrabberError(400, "Model scan directory could not be read") from None

    @staticmethod
    def _group(value):
        if (not isinstance(value, str) or not value or len(value) > 255
                or value in {".", ".."} or value.startswith(".")
                or any(c in value for c in "/\\\\%")
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise GrabberError(400, "group must be a single non-hidden download directory name")
        return value

    def _retag_start(self, body):
        allowed = {"group", "mode", "tag_format", "tagger_id", "tagger_config",
                   "confirm_replace", "confirm_remote"}
        if set(body) - allowed:
            raise GrabberError(400, "Retag accepts only group, mode, format, tagger and confirmations")
        group = self._group(body.get("group"))
        mode = body.get("mode", "merge")
        if not isinstance(mode, str) or mode not in {"merge", "replace", "missing"}:
            raise GrabberError(400, "Retag mode must be merge, replace or missing")
        tag_format = body.get("tag_format", "comma")
        if not isinstance(tag_format, str) or tag_format not in {"comma", "space"}:
            raise GrabberError(400, "tag_format must be comma or space")
        tagger = body.get("tagger_id")
        if not isinstance(tagger, str) or tagger not in {"local_onnx", "openai_compatible"}:
            raise GrabberError(400, "Retag requires local_onnx or openai_compatible, not none")
        for key in ("confirm_replace", "confirm_remote"):
            if key in body and not isinstance(body[key], bool):
                raise GrabberError(400, "Retag confirmations must be JSON booleans")
        if mode == "replace" and body.get("confirm_replace") is not True:
            raise GrabberError(400, "Replacing existing captions requires confirm_replace=true")
        if tagger == "openai_compatible" and body.get("confirm_remote") is not True:
            raise GrabberError(400, "Sending library images to the configured endpoint requires confirm_remote=true")
        merged = self._merge({"tagger_id": tagger, "tagger_config": body.get("tagger_config", {})},
                             source_required=False, allow_multi=False)
        merged.pop("images_only", None)
        merged.update(group=group, mode=mode, tag_format=tag_format,
                      confirm_replace=body.get("confirm_replace", False),
                      confirm_remote=body.get("confirm_remote", False))
        self._require_capabilities("library", "retag")
        if not self._task_lock.acquire(blocking=False):
            raise GrabberError(409, "Another start request is in progress")
        try:
            return self._request("/api/library/retag/start", merged, timeout=90)
        finally:
            self._task_lock.release()

    def annotation_request(self, method, route, query=None, body=None):
        """Strict, isolated bridge; never reuse legacy redaction or retry writes."""
        from http.client import HTTPException
        from .annotations import (AnnotationError, MAX_BODY, strict_json,
                                  validate_request, validate_response)

        params, clean_body = validate_request(method, route, query, body)
        with self._lock:
            process, port = self._process, self._port
            if self._closed or process is None or process.poll() is not None or port is None:
                raise AnnotationError(503, "UPSTREAM_UNAVAILABLE",
                                      "Start the managed Grabber explicitly", retryable=True)

        def exchange(path, request_method="GET", data=None):
            with self._lock:
                if (self._closed or self._process is not process or self._port != port
                        or process.poll() is not None):
                    raise AnnotationError(503, "UPSTREAM_UNAVAILABLE",
                                          "Managed Grabber is unavailable", retryable=True)
            request = Request(f"http://127.0.0.1:{port}{path}", data=data,
                              method=request_method, headers={"Content-Type": "application/json",
                                                              "Accept": "application/json"})
            try:
                try:
                    response = self._opener.open(request, timeout=30)
                except HTTPError as exc:
                    response = exc
                with response:
                    status = response.status
                    lengths = response.headers.get_all("Content-Length", [])
                    types = response.headers.get_all("Content-Type", [])
                    if (len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json"
                            or len(lengths) > 1 or (lengths and
                            (re.fullmatch(r"[0-9]{1,10}", lengths[0]) is None or
                             int(lengths[0]) > MAX_RESPONSE))):
                        raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream response headers")
                    raw = response.read(MAX_RESPONSE + 1)
                    if (len(raw) > MAX_RESPONSE or
                            (lengths and len(raw) != int(lengths[0]))):
                        raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream response size")
                payload = strict_json(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                return status, payload
            except (TimeoutError, URLError) as exc:
                timeout = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
                raise AnnotationError(504 if timeout else 503,
                                      "UPSTREAM_TIMEOUT" if timeout else "UPSTREAM_UNAVAILABLE",
                                      "Upstream timed out; reconcile mutations using the same requestId"
                                      if timeout else "Managed Grabber is unavailable",
                                      retryable=True) from None
            except HTTPException:
                raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Incomplete upstream response") from None
            except OSError:
                raise AnnotationError(503, "UPSTREAM_UNAVAILABLE", "Managed Grabber is unavailable",
                                      retryable=True) from None
            except (ValueError, UnicodeError, RecursionError):
                raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream JSON response") from None

        status, metadata = exchange("/api/sources")
        if status != 200:
            raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Capability negotiation failed")
        capabilities = metadata.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get("annotations_v1") is not True:
            raise AnnotationError(409, "UNSUPPORTED_CAPABILITY",
                                  "Update ArtistSourceGrabber for annotations_v1 and explicitly restart its managed service")
        data = None if clean_body is None else json.dumps(clean_body, ensure_ascii=False,
                                                         allow_nan=False).encode("utf-8")
        if data is not None and len(data) > MAX_BODY:
            raise AnnotationError(413, "BODY_TOO_LARGE", "Request body exceeds 1000000 bytes")
        path = "/api/annotations/" + route + ("?" + urlencode(params) if params else "")
        status, payload = exchange(path, method, data)
        return validate_response(method, route, status, payload, params if method == "GET" else clean_body)

    def get(self, route, query):
        if route == "status":
            return self.status()
        if route == "credentials":
            return credentials((query.get("source") or [""])[0])
        if route == "sources":
            return self._request("/api/sources")
        if route in {"library/groups", "library/items", "library/retag/progress"}:
            params = {}
            if route == "library/items":
                params["group"] = self._group((query.get("group") or [None])[0])
                for key, default in (("offset", "0"), ("limit", "60")):
                    raw = (query.get(key) or [default])[0]
                    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,10}", raw):
                        raise GrabberError(400, "Library pagination must use non-negative integers")
                    params[key] = int(raw)
                if not 1 <= params["limit"] <= 100:
                    raise GrabberError(400, "Library limit must be between 1 and 100")
            self._require_capabilities(*(("library", "retag") if route.endswith("progress") else ("library",)))
            return self._request("/api/" + route + ("?" + urlencode(params) if params else ""))
        if route == "progress":
            offsets = {}
            for key in ("logs", "items"):
                try:
                    offsets[key] = int((query.get(key) or ["0"])[0])
                except (ValueError, TypeError):
                    raise ValueError("progress offsets must be integers") from None
                if offsets[key] < 0:
                    raise ValueError("progress offsets must not be negative")
            return self._request("/api/progress?" + urlencode(offsets))
        raise GrabberError(404, "unknown grabber endpoint")

    def post(self, route, body):
        if route == "models/scan":
            return self.models_scan(body)
        if route == "library/retag/start":
            return self._retag_start(body)
        if route == "library/retag/stop":
            if (set(body) - {"id"} or "id" in body and
                    (not isinstance(body["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body["id"]))):
                raise GrabberError(400, "Retag stop accepts only an optional job ID")
            self._require_capabilities("library", "retag")
            return self._request("/api/library/retag/stop", body)
        if route == "service/start":
            return self.start_service()
        if route == "credentials":
            with _STORE_LOCK:
                self._remember(read_json(_store_path(), {}))
            self._remember(body.get("values", {}))
            return credentials(body.get("source"), body.get("values", {}), body.get("clear", []))
        if route in {"session/open", "session/check"}:
            source = body.get("source")
            if source not in {"twitter", "pixiv"}:
                raise ValueError("session source must be twitter or pixiv")
            prefix = "x" if source == "twitter" else "pixiv"
            return self._request(f"/api/{prefix}/{route}", {"artist": str(body.get("artist") or "")}, timeout=90)
        if route == "task/stop":
            return self._request("/api/stop", {})
        if route in {"source/test", "artists/search", "tagger/test", "task/start"}:
            merged = self._merge(body, source_required=route != "tagger/test", allow_multi=route == "task/start")
            if route == "task/start":
                if not self._task_lock.acquire(blocking=False):
                    raise GrabberError(409, "Another start request is in progress")
                try:
                    metadata = None
                    if merged["images_only"]:
                        metadata = self._require_capabilities("images_only")
                    if "sources" in merged:
                        self._check_multi(merged, metadata if metadata is not None else self._request("/api/sources"))
                    return self._request("/api/start", merged, timeout=90)
                finally:
                    self._task_lock.release()
            return self._request("/api/" + route, merged, timeout=90)
        raise GrabberError(404, "unknown grabber endpoint")

    @contextmanager
    def _open_media(self, raw):
        """Validate and no-follow open before reading or consulting any cache."""
        if not isinstance(raw, str) or not raw.startswith("/files/"):
            raise ValueError("expected an ASG /files/ path")
        decoded = unquote(raw, errors="strict")
        if re.search(r"%[0-9a-fA-F]{2}", decoded):
            raise ValueError("nested path encoding is not allowed")
        rel = decoded[len("/files/"):]
        if "\\" in rel or "\x00" in rel or any(p in {"", ".", ".."} for p in rel.split("/")):
            raise ValueError("invalid media path")
        root = self.download_dir
        target = (root / rel).resolve()
        if root not in target.parents:
            raise ValueError("media must stay inside downloads")
        # Reject links even within downloads, avoiding link-replacement races.
        current = root
        for part in rel.split("/"):
            current = current / part
            if current.is_symlink():
                raise ValueError("media symlinks are not supported")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
                ".txt": "text/plain; charset=utf-8"}.get(target.suffix.lower())
        if mime is None:
            raise ValueError("only raster images and txt captions may be previewed")
        # POSIX uses directory-relative, no-follow opens; Windows re-checks each
        # segment and then only trusts the fstat of the handle it actually reads.
        opener = _open_media_windows if os.name == "nt" else _open_media_posix
        try:
            with opener(root, rel) as (stream, info):
                yield stream, mime, info
        except OSError:
            raise GrabberError(404, "media file not found") from None

    def preview(self, raw, size="thumb"):
        if size not in SIZES:
            raise GrabberError(400, "Preview size must be thumb or detail")
        with self._open_media(raw) as (stream, mime, info):
            if not mime.startswith("image/"):
                raise GrabberError(400, "Preview requires a raster image")
            try:
                return self._previews.get(stream, mime, size, info)
            except PreviewError as exc:
                raise GrabberError(exc.status, str(exc)) from None

    def file(self, raw):
        """Original raster/caption bytes, unchanged; derivatives use preview()."""
        with self._open_media(raw) as (stream, mime, info):
            if info.st_size > MAX_FILE:
                raise GrabberError(413, "media exceeds 64 MiB")
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise GrabberError(413, "media exceeds 64 MiB")
        valid = (mime.startswith("text/plain") or
                 mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n") or
                 mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff") or
                 mime == "image/gif" and data[:6] in {b"GIF87a", b"GIF89a"} or
                 mime == "image/bmp" and data.startswith(b"BM") or
                 mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        if not valid:
            raise ValueError("media content does not match raster type")
        return data, mime
