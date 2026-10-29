"""Threaded stdlib HTTP server: JSON API, SSE, and the static frontend."""

import hmac
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import paths
from ..contract import (
    appearance,
    configs,
    datasets,
    doctor,
    grabber,
    history,
    knobs,
    launch,
    logs,
    runs,
)
from ..contract.annotations import (
    MAX_RESPONSE,
    AnnotationContract,
    AnnotationError,
    strict_json,
)
from ..contract.gbc_schema import VERSION as GBC_VERSION
from ..contract.gbc_schema import GbcError
from ..contract.root import TRAIN_SCRIPT, TrainerRoot
from ..store import notebook, profile, queue
from ..store.annotations import StoreError
from . import civitai

MAX_BODY = 1_000_000

COOKIE_NAME = "km_token"

# Windows resolves these suffixes from HKCR, where .js is often "text/plain" and
# .css/.json are unreliable; a wrong type makes the browser reject module scripts.
# The table wins over mimetypes, which stays as the fallback for everything else.
STATIC_MIME = {
    ".html": "text/html",
    ".htm": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain",
}


def _mime_for(name: str) -> str:
    """Content-Type for a static asset, with UTF-8 declared for text-like types."""
    suffix = Path(name).suffix.lower()
    mime = STATIC_MIME.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if mime.startswith("text/") or mime in {
        "application/javascript",
        "application/json",
        "text/javascript",
    }:
        mime += "; charset=utf-8"
    return mime


def cookie_token(header: str | None) -> str:
    """Pull the UI token out of a Cookie header (set on GET /)."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value.strip()
    return ""


class Api:
    """Request handlers with no framework and no third-party dependency."""

    def __init__(self, root: TrainerRoot, token: str):
        self.root = root
        self.grabber = grabber.Grabber(root)
        self.annotations = AnnotationContract(self.grabber)
        self._translation = None
        self._gbc = None
        self._translation_lock = threading.Lock()
        self._services_closed = False
        self.token = token

    @property
    def translation(self):
        # Existing API construction and shutdown must never touch private state.
        with self._translation_lock:
            if self._services_closed:
                raise AnnotationError(503, "UPSTREAM_UNAVAILABLE", "Service is closing", retryable=True)
            if self._translation is None:
                from ..contract.translation import TranslationService
                self._translation = TranslationService(self.annotations)
            return self._translation

    @property
    def gbc(self):
        with self._translation_lock:
            if self._services_closed:
                raise GbcError(503, "WORKER_UNAVAILABLE", "Service is closing")
            if self._gbc is None:
                from ..contract.gbc import GbcService
                self._gbc = GbcService(self.root)
            return self._gbc

    def close_services(self):
        with self._translation_lock:
            self._services_closed = True
            service = self._translation
            gbc_service = self._gbc
        try:
            if service is not None:
                service.close()
        finally:
            try:
                if gbc_service is not None:
                    gbc_service.close()
            finally:
                self.grabber.close()

    def token_ok(self, query: dict, header: str | None, cookie: str | None = None) -> bool:
        supplied = (query.get("token") or [""])[0] or header or cookie_token(cookie) or ""
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    def state(self) -> dict:
        return {
            "trainer": self.root.info(),
            "server_time": time.time(),
            "knob_script": TRAIN_SCRIPT,
        }

    def get_knobs(self, query: dict) -> dict:
        script = (query.get("script") or ["train"])[0]
        path = self.root.root / TRAIN_SCRIPT
        if script != "train":
            path = self.root.root / "scripts" / "sample" / "sample.py"
        payload = knobs.scan_script(path)
        payload["script"] = path.name
        return payload

    def get_configs(self) -> dict:
        items = configs.list_configs(self.root)
        user = [item for item in items if item["dir"].startswith("user")]
        recipes = [item for item in items if item["dir"].startswith("moss/recipes")]
        return {"configs": items, "user": user, "recipes": recipes}

    def load_config(self, query: dict) -> dict:
        rel = (query.get("path") or [""])[0]
        self._guard_config(rel)
        return {"path": rel, "values": configs.load_config(self.root, rel)}

    def check_config(self, query: dict) -> dict:
        rel = (query.get("path") or [""])[0]
        self._guard_config(rel)
        return configs.check_config(self.root, rel)

    def _guard_config(self, rel: str) -> None:
        if not rel:
            raise ValueError("path is required")
        target = (self.root.root / rel).resolve()
        configs_root = (self.root.root / "configs").resolve()
        if target != configs_root and configs_root not in target.parents:
            raise ValueError("config path must stay under configs/")

    def get_runs(self) -> dict:
        return {
            "runs": runs.list_runs(self.root),
            "runs_dir": str(self.root.runs_dir),
            "server_time": time.time(),
        }

    def get_run(self, query: dict) -> dict:
        name = (query.get("name") or [""])[0]
        return runs.detail(self.root, name)

    def get_log(self, query: dict) -> dict:
        name = (query.get("name") or [""])[0]
        offset = int((query.get("offset") or ["0"])[0])
        base = runs.run_path(self.root, name)
        new_offset, lines = logs.read_from(base / "log.txt", offset)
        return {"offset": new_offset, "lines": lines}

    def get_doctor(self, query: dict) -> dict:
        refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
        return doctor.report(self.root, refresh=refresh)

    def get_datasets(self, query: dict) -> dict:
        refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
        return datasets.scan(self.root, refresh=refresh)

    def start_run(self, body: dict) -> dict:
        if "draftRef" in body:
            if set(body) != {"draftRef"}:
                raise ValueError("draftRef cannot be combined with config, overrides, or name")
            self.gbc.get("capabilities")  # Reconnect the persisted draft resolver after restart.
            payload = launch.start(self.root, draft_ref=body["draftRef"])
        else:
            rel = str(body.get("config") or "")
            self._guard_config(rel)
            overrides = body.get("overrides") or {}
            if not isinstance(overrides, dict):
                raise ValueError("overrides must be an object")
            clean = {str(key): str(value) for key, value in overrides.items()}
            name = body.get("name") or None
            payload = launch.start(self.root, rel, clean, name)
        # A manual start must not fight the scheduler for the GPU.
        try:
            queue.pause_for_manual()
        except Exception:
            pass
        return payload

    def stop_run(self, body: dict) -> dict:
        name = str(body.get("name") or "")
        if not name:
            raise ValueError("name is required")
        return launch.stop(name, force=bool(body.get("force")))

    def file(self, query: dict):
        name = (query.get("name") or [""])[0]
        rel = (query.get("path") or [""])[0]
        target = runs.artifact_file(self.root, name, rel)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return target.read_bytes(), mime

    def get_appearance(self) -> dict:
        return appearance.read()

    def save_appearance(self, body: dict) -> dict:
        return appearance.save(body)

    def upload_background(self, data: bytes, mime: str) -> dict:
        return appearance.save_background(data, mime)

    def delete_background(self) -> dict:
        return appearance.remove_background()

    def background(self):
        found = appearance.background_file()
        if found is None:
            raise FileNotFoundError("background image")
        path, mime = found
        return path.read_bytes(), mime

    def get_profile(self) -> dict:
        payload = profile.read()
        try:
            payload["civitai"] = civitai.status()
        except Exception:
            payload["civitai"] = {"username": "", "hasKey": False, "keyHint": "", "lastSync": 0.0}
        payload["stats"] = self.profile_stats(payload)
        return payload

    def profile_stats(self, payload: dict) -> dict:
        """Roll the run history up into the four numbers the profile header shows."""
        stats = {"runs": 0, "steps": 0, "activeDays": 0, "published": None}
        try:
            totals = history.daily_activity(self.root).get("totals") or {}
            stats["runs"] = int(totals.get("runs") or 0)
            stats["steps"] = int(totals.get("steps") or 0)
            stats["activeDays"] = int(totals.get("days") or 0)
        except Exception:
            pass
        try:
            marks = civitai.publish_map(self.root)
            if civitai.status().get("lastSync"):
                stats["published"] = civitai.published_count(marks)
        except Exception:
            pass
        return stats

    def get_publish(self, query=None) -> dict:
        """Query Civitai automatically; refresh explicitly bypasses its cache."""
        info = civitai.status()
        error = ""
        cached = False
        if info.get("username"):
            refresh = ((query or {}).get("refresh") or [""])[0] == "1"
            result = civitai.sync(self.root, refresh=refresh)
            marks = result["publish"]
            error = result["error"]
            cached = result["cached"]
            info = civitai.status()
        else:
            marks = civitai.publish_map(self.root)
        return {"publish": marks, "civitai": info, "error": error, "cached": cached,
                "count": civitai.published_count(marks) if info.get("lastSync") else None}

    def save_profile(self, body: dict) -> dict:
        profile.save(body)
        return self.get_profile()

    def upload_avatar(self, data: bytes, mime: str) -> dict:
        return profile.save_avatar(data, mime)

    def delete_avatar(self) -> dict:
        profile.remove_avatar()
        return self.get_profile()

    def avatar(self):
        found = profile.avatar_file()
        if found is None:
            raise FileNotFoundError("avatar")
        path, mime = found
        return path.read_bytes(), mime

    def get_heatmap(self, query: dict) -> dict:
        raw = (query.get("days") or [""])[0]
        try:
            span = int(raw)
        except (TypeError, ValueError):
            span = history.DEFAULT_DAYS
        year = (query.get("year") or [None])[0]
        return history.daily_activity(self.root, days=span, year=year)

    def get_history(self) -> dict:
        return history.run_history(self.root)

    def get_notebook(self) -> dict:
        return notebook.read()

    def notebook_add(self, body: dict) -> dict:
        item = body.get("item")
        notebook.add(item if isinstance(item, dict) else body)
        return notebook.read()

    def notebook_update(self, body: dict) -> dict:
        patch = body.get("patch")
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        notebook.update(str(body.get("id") or ""), patch)
        return notebook.read()

    def notebook_remove(self, body: dict) -> dict:
        notebook.remove(str(body.get("id") or ""))
        return notebook.read()

    def get_queue(self) -> dict:
        return queue.view(queue.read())

    def queue_write(self, action: str, body: dict) -> dict:
        item_id = str(body.get("id") or "")
        if action == "add":
            draft = body.get("draft")
            if not isinstance(draft, dict):
                raise ValueError("draft must be an object")
            queue.add(draft)
        elif action == "move":
            queue.move(item_id, str(body.get("direction") or "up"))
        elif action == "update":
            patch = body.get("patch")
            if not isinstance(patch, dict):
                raise ValueError("patch must be an object")
            queue.update(item_id, patch)
        elif action == "remove":
            queue.remove(item_id)
        elif action == "control":
            queue.control(str(body.get("action") or ""), item_id, stopper=launch.stop)
        else:
            raise ValueError(f"unknown queue action {action}")
        return self.get_queue()

    def save_civitai_credentials(self, body: dict) -> dict:
        return civitai.save_credentials(body.get("username"), body.get("apiKey"))

    def delete_civitai_credentials(self) -> dict:
        return civitai.delete_credentials()

    def sync_civitai(self, body: dict) -> dict:
        return civitai.sync(self.root, refresh=bool(body.get("refresh")))


class Handler(BaseHTTPRequestHandler):
    server_version = "KohakuMossUI/0.1"
    api: Api = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args) -> None:
        if (self.path.startswith(("/api/grabber/", "/api/translation/", "/api/gbc/"))
                or (self.path.startswith("/api/") and self.command == "GET")):
            return
        super().log_message(fmt, *args)

    def _query(self) -> dict:
        return parse_qs(urlsplit(self.path).query)

    def _send(self, status: int, body: bytes, mime: str = "application/json", extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        if not any(key.lower() == "cache-control" for key in (extra or {})):
            self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    @staticmethod
    def _workbench_path(path):
        return any(path == prefix or path.startswith(prefix + "/") for prefix in
                   ("/api/grabber/annotations", "/api/translation", "/api/gbc/v1"))

    def _workbench_json(self, status, payload):
        if urlsplit(self.path).path.startswith("/api/gbc/v1"):
            payload = {"apiVersion": GBC_VERSION, **payload}
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) > MAX_RESPONSE:
            raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Response exceeds 16 MiB")
        extra = None
        if urlsplit(self.path).path in {"/api/gbc/v1/datasets/media", "/api/gbc/v1/graphs/media"}:
            extra = {"X-Content-Type-Options": "nosniff",
                     "Content-Security-Policy": "default-src 'none'; sandbox"}
        self._send(status, raw, extra=extra)

    def _annotations(self, path, query=None, body=None):
        route = path.removeprefix("/api/grabber/annotations/")
        service = self.api.annotations
        result = service.post(route, body) if body is not None else service.get(route, query or {})
        self._workbench_json(*result)

    def _translation(self, path, query=None, body=None):
        route = path.removeprefix("/api/translation/")
        service = self.api.translation
        result = service.post(route, body) if body is not None else service.get(route, query or {})
        self._workbench_json(*result)

    def _workbench(self):
        """New endpoints have strict parsing without changing any legacy API."""
        parsed = urlsplit(self.path)
        if not self._workbench_path(parsed.path):
            return False
        self.close_connection = True
        try:
            try:
                if re.search(r"%(?![0-9a-fA-F]{2})", parsed.query):
                    raise ValueError("Malformed percent encoding")
                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                                 encoding="utf-8", errors="strict", max_num_fields=32)
                if any(len(values) != 1 for values in query.values()):
                    raise ValueError("Repeated query parameter")
            except (ValueError, UnicodeError):
                raise AnnotationError(400, "INVALID_REQUEST", "Invalid or repeated query parameters") from None
            try:
                if parsed.path.startswith("/api/gbc/v1"):
                    tokens = self.headers.get_all("X-UI-Token", [])
                    hosts = self.headers.get_all("Host", [])
                    port = self.server.server_address[1]
                    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
                    authenticated = ("token" not in query and len(tokens) == 1 and
                                     self.api.token_ok({}, tokens[0]))
                    if len(hosts) != 1 or hosts[0] not in allowed_hosts:
                        raise GbcError(403, "PERMISSION_DENIED", "Loopback Host required")
                    origins = self.headers.get_all("Origin", [])
                    if origins and (len(origins) != 1 or origins[0] != "http://" + hosts[0]):
                        raise GbcError(403, "PERMISSION_DENIED", "Same-origin request required")
                else:
                    authenticated = self.api.token_ok(query, self.headers.get("X-UI-Token"), self.headers.get("Cookie"))
            except (ValueError, TypeError):
                authenticated = False
            if not authenticated:
                raise AnnotationError(403, "AUTH_REQUIRED", "UI authentication required")
            query.pop("token", None)
            if self.command not in {"GET", "POST"}:
                raise AnnotationError(405, "METHOD_NOT_ALLOWED", "Only GET and POST are supported")
            if parsed.fragment:
                raise AnnotationError(400, "INVALID_REQUEST", "Fragments are not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get_all("Transfer-Encoding") or len(lengths) > 1 or
                    (lengths and re.fullmatch(r"[0-9]{1,10}", lengths[0]) is None)):
                raise AnnotationError(400, "INVALID_REQUEST", "Invalid request framing")
            length = int(lengths[0]) if lengths else 0
            if length > MAX_BODY:
                raise AnnotationError(413, "BODY_TOO_LARGE", "Request body exceeds 1000000 bytes")
            body = None
            if self.command == "POST":
                if query:
                    raise AnnotationError(400, "INVALID_REQUEST", "POST does not accept query fields")
                types = self.headers.get_all("Content-Type", [])
                if (len(types) != 1 or re.fullmatch(
                        r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*',
                        types[0], re.IGNORECASE) is None):
                    raise AnnotationError(400, "INVALID_REQUEST", "Content-Type must be application/json UTF-8")
                if len(lengths) != 1:
                    raise AnnotationError(400, "INVALID_REQUEST", "Content-Length is required")
                previous_timeout = self.connection.gettimeout()
                try:
                    self.connection.settimeout(30)
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("Incomplete body")
                    body = strict_json(raw)
                    if not isinstance(body, dict):
                        raise ValueError("Expected an object")
                except (ValueError, UnicodeError, RecursionError, OSError):
                    raise AnnotationError(400, "INVALID_JSON", "Request must contain one complete JSON object") from None
                finally:
                    self.connection.settimeout(previous_timeout)
            elif length:
                raise AnnotationError(400, "INVALID_REQUEST", "GET does not accept a body")
            if parsed.path.startswith("/api/grabber/annotations"):
                self._annotations(parsed.path, query=query, body=body)
            elif parsed.path.startswith("/api/gbc/v1"):
                route = parsed.path.removeprefix("/api/gbc/v1/")
                service = self.api.gbc
                result = service.post(route, body) if body is not None else service.get(route, query)
                self._workbench_json(*result)
            else:
                self._translation(parsed.path, query=query, body=body)
        except (AnnotationError, GbcError, StoreError) as exc:
            self._workbench_json(exc.status, exc.payload)
        except Exception:
            error = AnnotationError(500, "INTERNAL", "Annotation workbench operation failed")
            self._workbench_json(error.status, error.payload)
        return True

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler otherwise emits HTML for unimplemented verbs.
        if self._workbench_path(urlsplit(getattr(self, "path", "")).path):
            if code == 501:
                self._workbench()
            else:
                error = AnnotationError(400, "INVALID_REQUEST", "Invalid HTTP request")
                self._workbench_json(error.status, error.payload)
            return
        super().send_error(code, message, explain)

    def do_GET(self) -> None:
        if self._workbench():
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith("/api/"):
            if not self.api.token_ok(query, self.headers.get("X-UI-Token"), self.headers.get("Cookie")):
                self._error(403, "bad token")
                return
            try:
                self._api_get(path, query)
            except FileNotFoundError as exc:
                self._error(404, f"not found: {exc}")
            except (ValueError, KeyError) as exc:
                self._error(400, str(exc))
            except Exception as exc:
                self._error(500, f"{type(exc).__name__}: {exc}")
            return

        if path == "/":
            if not self.api.token_ok(query, self.headers.get("X-UI-Token"), self.headers.get("Cookie")):
                self._send(403, b"open the URL printed by kohakumoss-ui (it carries the token)\n", "text/plain; charset=utf-8")
                return
            # Session cookie (no Max-Age) so a plain reload keeps working after
            # the frontend strips the token from the address bar.
            cookie = f"{COOKIE_NAME}={self.api.token}; Path=/; HttpOnly; SameSite=Strict"
            self._static("index.html", {"Set-Cookie": cookie})
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._static(path.lstrip("/")[7:] if path.startswith("/assets/") else path.lstrip("/"))

    def _grabber(self, path, query=None, body=None):
        """Keep upstream failures out of the generic exception/response path."""
        route = path.removeprefix("/api/grabber/")
        try:
            if body is not None:
                self._json(self.api.grabber.post(route, body))
            elif route == "preview":
                params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if any(len(params.get(key, [])) != 1 for key in ("path",)) or len(params.get("size", ["thumb"])) != 1:
                    raise ValueError("invalid preview parameters")
                preview = self.api.grabber.preview(params["path"][0], params.get("size", ["thumb"])[0])
                validators = [value.strip().removeprefix("W/") for value in
                              self.headers.get("If-None-Match", "").split(",")]
                unchanged = preview.etag in validators or "*" in validators
                self._send(304 if unchanged else 200, b"" if unchanged else preview.data, preview.mime, {
                    "Cache-Control": "private, no-cache",
                    "ETag": preview.etag,
                    "Vary": "Cookie, X-UI-Token",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                    "Content-Disposition": "inline",
                    "X-Preview-Width": str(preview.width),
                    "X-Preview-Height": str(preview.height),
                })
            elif route == "file":
                data, mime = self.api.grabber.file((query.get("path") or [""])[0])
                self._send(200, data, mime, {
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                    "Content-Disposition": "inline",
                })
            else:
                self._json(self.api.grabber.get(route, query or {}))
        except grabber.GrabberError as exc:
            self._json(exc.payload, exc.status)
        except (ValueError, KeyError):
            self._error(400, "Invalid grabber source, fields, offsets or media path")
        except Exception:
            self._error(500, "Grabber operation failed; check local configuration")

    def _api_get(self, path: str, query: dict) -> None:
        if path.startswith("/api/grabber/"):
            self._grabber(path, query=query)
            return
        api = self.api
        match path:
            case "/api/state":
                self._json(api.state())
            case "/api/knobs":
                self._json(api.get_knobs(query))
            case "/api/configs":
                self._json(api.get_configs())
            case "/api/config/load":
                self._json(api.load_config(query))
            case "/api/config/check":
                self._json(api.check_config(query))
            case "/api/runs":
                self._json(api.get_runs())
            case "/api/run":
                self._json(api.get_run(query))
            case "/api/run/log":
                self._json(api.get_log(query))
            case "/api/doctor":
                self._json(api.get_doctor(query))
            case "/api/datasets":
                self._json(api.get_datasets(query))
            case "/api/file":
                body, mime = api.file(query)
                self._send(200, body, mime)
            case "/api/appearance":
                self._json(api.get_appearance())
            case "/api/appearance/background":
                body, mime = api.background()
                self._send(200, body, mime)
            case "/api/profile":
                self._json(api.get_profile())
            case "/api/profile/avatar":
                body, mime = api.avatar()
                self._send(200, body, mime)
            case "/api/heatmap":
                self._json(api.get_heatmap(query))
            case "/api/history":
                self._json(api.get_history())
            case "/api/notebook":
                self._json(api.get_notebook())
            case "/api/publish":
                self._json(api.get_publish(query))
            case "/api/queue":
                self._json(api.get_queue())
            case "/api/events":
                self._sse(query)
            case _:
                self._error(404, f"unknown endpoint {path}")

    def _static(self, rel: str, extra: dict | None = None) -> None:
        if not rel:
            rel = "index.html"
        target = (paths.WEB_DIR / rel).resolve()
        web = paths.WEB_DIR.resolve()
        if target != web and web not in target.parents:
            self._error(404, "not found")
            return
        if not target.is_file():
            self._error(404, f"not found: {rel}")
            return
        self._send(200, target.read_bytes(), _mime_for(target.name), extra)

    def _sse(self, query: dict) -> None:
        name = (query.get("name") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        offset = 0
        log_path = None
        if name:
            try:
                log_path = runs.run_path(self.api.root, name) / "log.txt"
                offset = log_path.stat().st_size
            except (OSError, ValueError):
                log_path = None

        def emit(payload: dict) -> bool:
            chunk = f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n".encode()
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        if not emit({"type": "hello", "server_time": time.time()}):
            return
        try:
            last_version = int(queue.view(queue.read()).get("version") or 0)
        except Exception:
            last_version = -1
        last_beat = time.time()
        while True:
            payload: dict = {"type": "tick", "server_time": time.time()}
            try:
                summary = runs.summarize(self.api.root, runs.run_path(self.api.root, name))
                payload["summary"] = summary
            except (OSError, ValueError):
                payload["summary"] = None
            if log_path is not None:
                offset, lines = logs.read_from(log_path, offset)
                if lines:
                    payload["lines"] = lines
            if not emit(payload):
                return
            try:
                snapshot = queue.view(queue.read())
            except Exception:
                snapshot = None
            if snapshot is not None and int(snapshot.get("version") or 0) != last_version:
                last_version = int(snapshot.get("version") or 0)
                event = {"type": "queue", "server_time": time.time()}
                event.update(snapshot)
                if not emit(event):
                    return
            if time.time() - last_beat > 15:
                last_beat = time.time()
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
            time.sleep(1.0)

    def do_POST(self) -> None:
        if self._workbench():
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if not self.api.token_ok(query, self.headers.get("X-UI-Token"), self.headers.get("Cookie")):
            self._error(403, "bad token")
            return
        if parsed.path == "/api/appearance/background":
            self._upload_background()
            return
        if parsed.path == "/api/profile/avatar":
            self._upload_avatar()
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length < 0:
                raise ValueError("invalid content length")
            if length > MAX_BODY:
                self._error(413, "body too large")
                return
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must be a JSON object") from exc
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            if parsed.path.startswith("/api/grabber/"):
                self._grabber(parsed.path, body=body)
                return
            match parsed.path:
                case "/api/run/start":
                    self._json(self.api.start_run(body))
                case "/api/run/stop":
                    self._json(self.api.stop_run(body))
                case "/api/appearance":
                    self._json(self.api.save_appearance(body))
                case "/api/profile":
                    self._json(self.api.save_profile(body))
                case "/api/notebook/add":
                    self._json(self.api.notebook_add(body))
                case "/api/notebook/update":
                    self._json(self.api.notebook_update(body))
                case "/api/notebook/remove":
                    self._json(self.api.notebook_remove(body))
                case "/api/publish":
                    self._error(405, "publish status is read-only; query Civitai")
                case "/api/queue/add":
                    self._json(self.api.queue_write("add", body))
                case "/api/queue/move":
                    self._json(self.api.queue_write("move", body))
                case "/api/queue/update":
                    self._json(self.api.queue_write("update", body))
                case "/api/queue/remove":
                    self._json(self.api.queue_write("remove", body))
                case "/api/queue/control":
                    self._json(self.api.queue_write("control", body))
                case "/api/civitai/credentials":
                    self._json(self.api.save_civitai_credentials(body))
                case "/api/civitai/sync":
                    self._json(self.api.sync_civitai(body))
                case _:
                    self._error(404, f"unknown endpoint {parsed.path}")
        except GbcError as exc:
            self._json(exc.payload, exc.status)
        except FileNotFoundError as exc:
            self._error(404, f"not found: {exc}")
        except (ValueError, KeyError) as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _upload_background(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._error(400, "bad content length")
            return
        if length <= 0:
            self._error(400, "empty image body")
            return
        if length > appearance.MAX_UPLOAD:
            self._error(413, "image is larger than 25 MB")
            return
        mime = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            self._json(self.api.upload_background(self.rfile.read(length), mime))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _upload_avatar(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._error(400, "bad content length")
            return
        if length <= 0:
            self._error(400, "empty image body")
            return
        if length > profile.MAX_UPLOAD:
            self._error(413, "image is larger than 5 MB")
            return
        mime = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            self._json(self.api.upload_avatar(self.rfile.read(length), mime))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_DELETE(self) -> None:
        if self._workbench():
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if not self.api.token_ok(query, self.headers.get("X-UI-Token"), self.headers.get("Cookie")):
            self._error(403, "bad token")
            return
        try:
            if parsed.path == "/api/appearance/background":
                self._json(self.api.delete_background())
                return
            if parsed.path == "/api/profile/avatar":
                self._json(self.api.delete_avatar())
                return
            if parsed.path == "/api/civitai/credentials":
                self._json(self.api.delete_civitai_credentials())
                return
            self._error(404, f"unknown endpoint {parsed.path}")
        except (ValueError, KeyError) as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}: {exc}")


def make_server(root: TrainerRoot, host: str, port: int, token: str) -> ThreadingHTTPServer:
    """Bind the UI server; port 0 lets the OS pick a free one."""
    api = Api(root, token)
    handler = type("BoundHandler", (Handler,), {"api": api})
    class WorkbenchServer(ThreadingHTTPServer):
        # SO_REUSEADDR on Windows lets a second instance steal a live port.
        allow_reuse_address = os.name != "nt"

        def server_close(self):
            try:
                api.close_services()
            finally:
                super().server_close()

    server = WorkbenchServer((host, port), handler)
    server.daemon_threads = True
    return server
