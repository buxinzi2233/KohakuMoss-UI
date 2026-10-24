"""Offline-first, immutable SQLite dictionaries and private user overrides.

Only install() performs network IO, and only for an explicitly pinned source.
This module adds no wire routes. Per-segment manual translations belong to the
translation service, not the dictionary override layer.
"""

import contextlib
import functools
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from ..store.annotations import StoreError

REPOSITORY_URL = "https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table"
REPOSITORY_PATH = "/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table"
COLUMNS = ("name", "category", "cn_name", "post_count")
MAX_DATABASE = 256 * 1024 * 1024
TIMEOUT = 60
_VERSION = re.compile(r"dict_[0-9a-f]{64}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class LexiconError(Exception):
    def __init__(self, status, code, message, details=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = {"ok": False, "error": {"code": code, "message": message,
                        "details": details or {}, "retryable": retryable}}


def _invalid():
    return LexiconError(422, "DICTIONARY_INVALID", "Dictionary is invalid")


def _guard(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except StoreError as exc:
            error = exc.payload["error"]
            raise LexiconError(exc.status, exc.code, error["message"],
                               error["details"], error["retryable"]) from None
        except (sqlite3.Error, UnicodeError, ValueError, OverflowError):
            raise _invalid() from None
        except OSError:
            raise LexiconError(507, "STORAGE_FULL", "Dictionary IO failed") from None
    return wrapped


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def dictionary_version(sqlite_sha256, table_name):
    return "dict_" + hashlib.sha256(_canonical({"sqliteSha256": sqlite_sha256,
                      "tableName": table_name, "schemaVersion": 1})).hexdigest()


def _check_cancel(cancel, deadline=None):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise LexiconError(409, "CANCELLED", "Dictionary operation cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise LexiconError(504, "REMOTE_TIMEOUT", "Dictionary operation timed out", retryable=True)


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _key(text):
    if not isinstance(text, str):
        raise LexiconError(400, "INVALID_REQUEST", "Lookup text must be a string")
    text.encode("utf-8")
    return text.strip().replace(" ", "_").replace("\\(", "(").replace("\\)", ")")


@contextlib.contextmanager
def _source(path):
    """Explicit user import: no symlink component and no special final file."""
    if not isinstance(path, (str, os.PathLike)):
        raise LexiconError(400, "UNSAFE_PATH", "Invalid dictionary path")
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise LexiconError(400, "UNSAFE_PATH", "Dictionary path must be absolute")
    fd = None
    parent = None
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise LexiconError(403, "UNSAFE_STORAGE", "Safe filesystem operations unavailable")
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LexiconError(400, "UNSAFE_PATH", "Dictionary must be a single-link regular file")
        if info.st_size > MAX_DATABASE:
            raise LexiconError(413, "LIMIT_EXCEEDED", "Dictionary is too large")
        with os.fdopen(os.dup(fd), "rb") as stream:
            yield stream
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise LexiconError(409, "VERSION_CONFLICT", "Import file changed while reading")
    except OSError:
        raise LexiconError(400, "UNSAFE_PATH", "Dictionary path cannot be safely read") from None
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


@contextlib.contextmanager
def _database(path, cancel=None, deadline=None):
    uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True, timeout=0)
    interrupted = []
    try:
        db.enable_load_extension(False)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
        db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 32)
        def progress():
            try:
                _check_cancel(cancel, deadline)
                return 0
            except LexiconError as exc:
                interrupted.append(exc)
                return 1
        db.set_progress_handler(progress, 1000)
        try:
            yield db
        except sqlite3.Error:
            if interrupted:
                raise interrupted[0] from None
            raise
    finally:
        db.close()


def _probe(path, table_name, cancel=None, deadline=None):
    _check_cancel(cancel, deadline)
    with _database(path, cancel, deadline) as db:
        if db.execute("PRAGMA integrity_check").fetchmany(2) != [("ok",)]:
            raise _invalid()
        tables = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchmany(129)
        if len(tables) > 128:
            raise _invalid()
        candidates = {}
        for name, sql in tables:
            _check_cancel(cancel, deadline)
            if name.startswith("sqlite_") or not sql or "VIRTUAL TABLE" in sql.upper():
                continue
            columns = db.execute("PRAGMA table_xinfo(" + _quote(name) + ")").fetchall()
            if any(col[6] != 0 for col in columns):
                continue  # Generated columns can execute untrusted expressions.
            schema = {col[1]: col[2].upper() for col in columns}
            if not set(COLUMNS).issubset(schema):
                continue
            if (not all(any(affinity in schema[col] for affinity in ("CHAR", "CLOB", "TEXT"))
                        for col in ("name", "cn_name"))
                    or not all("INT" in schema[col] for col in ("category", "post_count"))):
                continue
            candidates[name] = list(COLUMNS)
        if not candidates:
            raise _invalid()
        if table_name is None:
            if len(candidates) != 1:
                raise LexiconError(422, "DICTIONARY_TABLE_REQUIRED", "Choose a dictionary table",
                                   {"tables": sorted(candidates)})
            table_name = next(iter(candidates))
        if not isinstance(table_name, str) or table_name not in candidates:
            raise _invalid()
        cursor = db.execute("SELECT name, category, cn_name, post_count FROM " + _quote(table_name))
        names = set()
        count = 0
        for name, category, target, posts in cursor:
            _check_cancel(cancel, deadline)
            if (not isinstance(name, str) or not name or not isinstance(target, str)
                    or (category is not None and type(category) is not int)
                    or (posts is not None and type(posts) is not int) or name in names):
                raise _invalid()
            name.encode("utf-8")
            target.encode("utf-8")
            names.add(name)
            count += 1
        return table_name, candidates[table_name], count


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LexiconError(502, "REMOTE_FAILED", "Dictionary redirects are not allowed")


def _download_target(url, ref, digest):
    if (not isinstance(ref, str) or not _COMMIT.fullmatch(ref)
            or not isinstance(digest, str) or not _HASH.fullmatch(digest)
            or not isinstance(url, str) or len(url) > 2048
            or not url.isascii() or any(ord(ch) < 33 for ch in url)):
        raise LexiconError(400, "INVALID_REQUEST", "Pinned commit and SHA256 are required")
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.query or parts.fragment or parts.port not in (None, 443)
            or "%" in parts.path or "\\" in parts.path
            or any(piece in (".", "..", "") for piece in parts.path.split("/")[1:])):
        raise LexiconError(400, "UNSAFE_PATH", "Dictionary URL is not allowed")
    if parts.hostname == "raw.githubusercontent.com":
        prefix = REPOSITORY_PATH + "/" + ref + "/"
    elif parts.hostname == "github.com":
        prefix = REPOSITORY_PATH + "/raw/" + ref + "/"
    else:
        raise LexiconError(400, "UNSAFE_PATH", "Dictionary URL is not allowed")
    if not parts.path.startswith(prefix) or not parts.path[len(prefix):]:
        raise LexiconError(400, "UNSAFE_PATH", "Dictionary URL must contain the pinned commit")


class Lexicon:
    def __init__(self, store):
        self.store = store

    def _validate_manifest(self, manifest):
        required = {"dictionaryVersion", "sqliteSha256", "importedAt", "tableName", "columns",
                    "repositoryUrl", "ref", "license", "rows", "schemaVersion"}
        if (not isinstance(manifest, dict) or set(manifest) != required
                or not isinstance(manifest["sqliteSha256"], str)
                or not _HASH.fullmatch(manifest["sqliteSha256"])
                or not isinstance(manifest["tableName"], str) or not manifest["tableName"]
                or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
                or type(manifest["rows"]) is not int or manifest["rows"] < 0
                or type(manifest["importedAt"]) is not int or manifest["importedAt"] < 0
                or manifest["columns"] != list(COLUMNS)
                or manifest["repositoryUrl"] != REPOSITORY_URL or manifest["license"] != "MIT"
                or (manifest["ref"] is not None and not isinstance(manifest["ref"], str))
                or dictionary_version(manifest["sqliteSha256"], manifest["tableName"])
                != manifest["dictionaryVersion"]):
            raise _invalid()
        return manifest

    @_guard
    def manifest(self, version=None):
        with self.store.lock:
            if version is None:
                envelope = self.store.read_record("dictionary", "current")
                value = envelope["record"] if envelope else None
            else:
                if not isinstance(version, str) or not _VERSION.fullmatch(version):
                    raise LexiconError(400, "INVALID_REQUEST", "Invalid dictionary version")
                value = self.store._read_json(("dictionary", "versions", version, "manifest.json"))
                if value is not None and value.get("dictionaryVersion") != version:
                    raise _invalid()
            return self._validate_manifest(value) if value is not None else None

    def status(self):
        result = {"status": "missing", "dictionaryVersion": None, "tableName": None,
                  "rows": 0, "ref": None, "repositoryUrl": REPOSITORY_URL,
                  "license": "MIT", "error": None}
        try:
            manifest = self.manifest()
            if manifest:
                with self.store._open_blob(("dictionary", "versions", manifest["dictionaryVersion"],
                                           "tag.sqlite"), MAX_DATABASE):
                    pass
                for field in ("dictionaryVersion", "tableName", "rows", "ref", "repositoryUrl", "license"):
                    result[field] = manifest[field]
                result["status"] = "ready"
        except (LexiconError, StoreError) as exc:
            result.update(status="error", error=exc.payload["error"])
        except FileNotFoundError:
            result.update(status="error", error=_invalid().payload["error"])
        return result

    def _baseline(self, expected_version):
        with self.store.lock:
            current = self.store.read_record("dictionary", "current")
            version = self._validate_manifest(current["record"])["dictionaryVersion"] if current else None
            if expected_version is not None and version != expected_version:
                raise LexiconError(409, "VERSION_CONFLICT", "Dictionary version changed")
            return current

    @staticmethod
    def _copy(source, destination, cancel, deadline):
        digest = hashlib.sha256()
        size = 0
        while True:
            _check_cancel(cancel, deadline)
            block = source.read(min(1024 * 1024, MAX_DATABASE + 1 - size))
            if not block:
                break
            size += len(block)
            if size > MAX_DATABASE:
                raise LexiconError(413, "LIMIT_EXCEEDED", "Dictionary is too large")
            digest.update(block)
            destination.write(block)
        destination.flush()
        os.fsync(destination.fileno())
        _check_cancel(cancel, deadline)
        return digest.hexdigest()

    def _publish(self, scratch, digest, table_name, ref, current, cancel, deadline):
        table_name, columns, rows = _probe(scratch, table_name, cancel, deadline)
        version = dictionary_version(digest, table_name)
        manifest = {"dictionaryVersion": version, "sqliteSha256": digest,
                    "importedAt": int(time.time()), "tableName": table_name,
                    "columns": columns, "repositoryUrl": REPOSITORY_URL,
                    "ref": ref, "license": "MIT", "rows": rows, "schemaVersion": 1}
        with self.store.lock:
            _check_cancel(cancel, deadline)
            self.store.acquire_owner()
            latest = self.store.read_record("dictionary", "current")
            if latest != current:
                raise LexiconError(409, "VERSION_CONFLICT", "Dictionary version changed")
            prefix = ("dictionary", "versions", version)
            try:
                with self.store._open_blob(prefix + ("tag.sqlite",), MAX_DATABASE) as fd:
                    with os.fdopen(os.dup(fd), "rb") as existing:
                        actual = hashlib.file_digest(existing, "sha256").hexdigest()
                    if actual != digest:
                        raise _invalid()
            except FileNotFoundError:
                with open(scratch, "rb") as source:
                    self.store._write_stream(prefix + ("tag.sqlite",), source,
                                             MAX_DATABASE, exclusive=True)
            previous = self.manifest(version)
            if previous:
                manifest = previous
            else:
                self.store._write_json(prefix + ("manifest.json",), manifest)
            _check_cancel(cancel, deadline)
            if current and current["record"]["dictionaryVersion"] == version:
                return current["record"]
            self.store.compare_write("dictionary", "current", current["revision"] if current else 0, manifest)
            return manifest

    @_guard
    def import_file(self, path, table_name=None, cancel=None, expected_version=None):
        current = self._baseline(expected_version)
        deadline = time.monotonic() + TIMEOUT
        with tempfile.TemporaryDirectory(prefix="kohakumoss-dictionary-") as folder:
            scratch = Path(folder) / "import.sqlite"
            with _source(path) as source:
                with open(scratch, "xb") as target:
                    os.fchmod(target.fileno(), 0o600)
                    digest = self._copy(source, target, cancel, deadline)
            return self._publish(scratch, digest, table_name, None, current, cancel, deadline)

    @_guard
    def install(self, download_url, ref, expected_sha256, table_name=None,
                cancel=None, expected_version=None):
        _download_target(download_url, ref, expected_sha256)
        current = self._baseline(expected_version)
        deadline = time.monotonic() + TIMEOUT
        _check_cancel(cancel, deadline)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        request = urllib.request.Request(download_url, headers={"Accept-Encoding": "identity"})
        with tempfile.TemporaryDirectory(prefix="kohakumoss-dictionary-") as folder:
            scratch = Path(folder) / "download.sqlite"
            try:
                with opener.open(request, timeout=TIMEOUT) as response:
                    if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                        raise LexiconError(502, "REMOTE_FAILED", "Dictionary download rejected")
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isdecimal() or int(length) > MAX_DATABASE):
                        raise LexiconError(413, "LIMIT_EXCEEDED", "Dictionary is too large")
                    with open(scratch, "xb") as target:
                        os.fchmod(target.fileno(), 0o600)
                        # Each read uses only the remaining global deadline,
                        # not a fresh sixty-second timeout for every chunk.
                        class Download:
                            def read(self, count):
                                _check_cancel(cancel, deadline)
                                sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                                if sock is not None:
                                    sock.settimeout(max(0.001, deadline - time.monotonic()))
                                return response.read1(count)
                        digest = self._copy(Download(), target, cancel, deadline)
                    if length is not None and scratch.stat().st_size != int(length):
                        raise LexiconError(502, "REMOTE_FAILED", "Dictionary download was incomplete")
            except (TimeoutError, socket.timeout):
                raise LexiconError(504, "REMOTE_TIMEOUT", "Dictionary download timed out", retryable=True) from None
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    raise LexiconError(504, "REMOTE_TIMEOUT", "Dictionary download timed out", retryable=True) from None
                raise LexiconError(502, "REMOTE_FAILED", "Dictionary download failed") from None
            except http.client.HTTPException:
                raise LexiconError(502, "REMOTE_FAILED", "Dictionary download was incomplete") from None
            if digest != expected_sha256:
                raise _invalid()
            return self._publish(scratch, digest, table_name, ref, current, cancel, deadline)

    @_guard
    def lookup(self, text, dictionary_version=None):
        key = _key(text)
        with self.store.lock:
            manifest = self.manifest(dictionary_version)
            if manifest is None:
                return None
            # Version-pinned translation jobs/cache must depend only on that
            # immutable database. Legacy unversioned callers retain overrides.
            overrides = self.store._read_json(("dictionary", "overrides.json")) if dictionary_version is None else None
            if overrides is not None:
                if (set(overrides) != {"entries"} or not isinstance(overrides["entries"], dict)
                        or any(not isinstance(value, str) for value in overrides["entries"].values())):
                    raise _invalid()
                if key in overrides["entries"]:
                    value = overrides["entries"][key]
                    return {"targetText": value, "category": None, "postCount": None,
                            "override": True} if value else None
            version = manifest["dictionaryVersion"]
            try:
                with self.store._open_blob(("dictionary", "versions", version, "tag.sqlite"), MAX_DATABASE) as fd:
                    # Keep the validated fd alive. SQLite opens exactly this
                    # inode, not a replaceable user-controlled pathname.
                    proc_path = "/proc/self/fd/" + str(fd)
                    if not os.path.exists(proc_path):
                        raise LexiconError(403, "UNSAFE_STORAGE", "Safe SQLite descriptor access unavailable")
                    with _database(proc_path, deadline=time.monotonic() + TIMEOUT) as db:
                        rows = db.execute("SELECT cn_name, category, post_count FROM " +
                                          _quote(manifest["tableName"]) +
                                          " WHERE name = ? COLLATE BINARY LIMIT 2", (key,)).fetchall()
            except FileNotFoundError:
                raise _invalid() from None
            if len(rows) > 1:
                raise _invalid()
            if not rows:
                return None
            target, category, posts = rows[0]
            if (not isinstance(target, str)
                    or (category is not None and type(category) is not int)
                    or (posts is not None and type(posts) is not int)):
                raise _invalid()
            return {"targetText": target, "category": category, "postCount": posts} if target else None

    @_guard
    def set_override(self, text, target_text):
        key = _key(text)
        if not key or not isinstance(target_text, str) or len(target_text) > 8192:
            raise LexiconError(400, "INVALID_REQUEST", "Invalid dictionary override")
        target_text.encode("utf-8")
        with self.store.lock:
            self.store.acquire_owner()
            overrides = self.store._read_json(("dictionary", "overrides.json"))
            if overrides is None:
                overrides = {"entries": {}}
            if (set(overrides) != {"entries"} or not isinstance(overrides["entries"], dict)
                    or any(not isinstance(value, str) for value in overrides["entries"].values())):
                raise _invalid()
            overrides["entries"][key] = target_text
            self.store._write_json(("dictionary", "overrides.json"), overrides)
