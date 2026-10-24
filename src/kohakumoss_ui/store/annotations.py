"""Private annotation state. No filesystem access occurs at construction.

All operations use the same reentrant lock. Mutations also acquire a lifetime
nonblocking process lock. This does not lock out non-cooperating same-uid tools.
Internal blob helpers are not HTTP file or record routes.
"""

import contextlib
import errno
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from .. import paths

try:
    import fcntl
except ImportError:
    fcntl = None

MAX_RECORD = 10 * 1024 * 1024
KINDS = frozenset(("translations", "translation-plans", "translation-jobs",
                   "requests", "dictionary", "settings", "cache"))
_ID = re.compile(r"(lib|doc|plan|job|req)_[0-9a-f]{32}\Z")
_VERSION = re.compile(r"dict_[0-9a-f]{64}\Z")


class StoreError(Exception):
    def __init__(self, status, code, message, details=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = {"ok": False, "error": {
            "code": code, "message": message, "details": details or {},
            "retryable": retryable}}


def _unsafe():
    return StoreError(403, "UNSAFE_STORAGE", "Private storage is not safe")


def _io_error(exc):
    if exc.errno in (errno.ENOSPC, errno.EDQUOT, errno.EFBIG):
        return StoreError(507, "STORAGE_FULL", "Private storage is full")
    return _unsafe()


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _constant(value):
    raise ValueError("non-finite number")


def _encode(value):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise StoreError(400, "INVALID_JSON", "Invalid private record") from None
    if len(data) > MAX_RECORD:
        raise StoreError(507, "STORAGE_FULL", "Private record exceeds its limit")
    return data


def _decode(data):
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        # Reject escaped lone surrogates and overflowing finite JSON numbers.
        _encode(value)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, UnicodeError, RecursionError, StoreError):
        raise StoreError(409, "RECOVERY_REQUIRED", "Private record is corrupt") from None


def _regular(fd, limit):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise _unsafe()
    if info.st_size > limit:
        raise StoreError(507, "STORAGE_FULL", "Private file exceeds its limit")
    return info


def _supported():
    if (os.name != "posix" or fcntl is None
            or not all(hasattr(os, name) for name in
                       ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
            or not {os.open, os.mkdir, os.stat, os.unlink, os.link}.issubset(os.supports_dir_fd)):
        raise _unsafe()


class AnnotationStore:
    def __init__(self, state=None):
        self.lock = threading.RLock()
        self._state = Path(state) if state is not None else None
        self._owner_fd = None
        self._owner_pid = None
        self._identities = {}

    def _state_path(self):
        if self._state is not None:
            path = self._state
        else:
            # Match paths.state_dir without its eager, following mkdir. Securely
            # create/validate first; call that public locator only afterwards.
            override = os.environ.get("KOHAKUMOSS_UI_STATE", "").strip()
            base = os.environ.get("XDG_STATE_HOME", "").strip()
            path = (Path(override).expanduser() if override else
                    (Path(base).expanduser() if base else Path.home() / ".local" / "state")
                    / "kohakumoss-ui")
        if not path.is_absolute() or ".." in path.parts:
            raise _unsafe()
        return path

    @contextlib.contextmanager
    def _directory(self, parts=(), create=False):
        """Open every component from /, checking live directory identities."""
        _supported()
        state = self._state_path()
        components = state.parts[1:] + ("annotations",) + tuple(parts)
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        walked = []
        sticky_parent = False
        try:
            for index, component in enumerate(components):
                if (not isinstance(component, str) or component in ("", ".", "..")
                        or "/" in component or "\\" in component or "\x00" in component):
                    raise _unsafe()
                walked.append(component)
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY |
                                    os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY |
                                    os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                mode = stat.S_IMODE(info.st_mode)
                private = index >= len(state.parts) - 2
                sticky = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
                if sticky_parent and (info.st_uid != os.getuid() or mode != 0o700):
                    raise _unsafe()
                if private:
                    if info.st_uid != os.getuid() or mode != 0o700:
                        raise _unsafe()
                elif info.st_uid not in (0, os.getuid()) or (mode & 0o022 and not sticky):
                    raise _unsafe()
                sticky_parent = sticky and bool(mode & 0o022)
                identity = (info.st_dev, info.st_ino)
                name = tuple(walked)
                previous = self._identities.setdefault(name, identity)
                if previous != identity:
                    raise _unsafe()
            if self._state is None and create:
                if paths.state_dir() != state:
                    raise _unsafe()
                self._state = state
            yield fd
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise _io_error(exc) from None
        finally:
            os.close(fd)

    def _location(self, kind, key):
        if kind not in KINDS:
            raise StoreError(400, "INVALID_REQUEST", "Invalid record kind")
        if kind in ("dictionary", "settings", "cache"):
            if key != "current":
                raise StoreError(400, "INVALID_REQUEST", "Invalid record key")
            return {"dictionary": ("dictionary", "manifest.json"),
                    "settings": ("translation-settings.json",),
                    "cache": ("translation-cache.json",)}[kind]
        if kind == "translations":
            if (not isinstance(key, tuple) or len(key) != 2
                    or not self._id(key[0], "lib") or not self._id(key[1], "doc")):
                raise StoreError(400, "INVALID_REQUEST", "Invalid translation identity")
            return (kind, key[0], key[1] + ".json")
        prefix = {"translation-plans": "plan", "translation-jobs": "job", "requests": "req"}[kind]
        if not self._id(key, prefix):
            raise StoreError(400, "INVALID_REQUEST", "Invalid record identity")
        return (kind, key + ".json")

    @staticmethod
    def _id(value, prefix):
        return isinstance(value, str) and bool(_ID.fullmatch(value)) and value.startswith(prefix + "_")

    @contextlib.contextmanager
    def _open_blob(self, parts, limit=MAX_RECORD):
        """Internal safe fd reader; lock remains held until context exit."""
        with self.lock:
            with self._directory(parts[:-1]) as parent:
                try:
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise _io_error(exc) from None
                try:
                    _regular(fd, limit)
                    yield fd
                finally:
                    os.close(fd)

    def _read_json(self, parts):
        try:
            with self._open_blob(parts) as fd:
                with os.fdopen(os.dup(fd), "rb") as source:
                    data = source.read(MAX_RECORD + 1)
                if len(data) > MAX_RECORD:
                    raise StoreError(507, "STORAGE_FULL", "Private file exceeds its limit")
                return _decode(data)
        except FileNotFoundError:
            return None

    def _write_stream(self, parts, stream, limit=MAX_RECORD, exclusive=False):
        """Internal atomic blob writer, bounded, with file and parent fsync."""
        with self.lock:
            self.acquire_owner()
            try:
                with self._directory(parts[:-1], create=True) as parent:
                    try:
                        with self._open_blob(parts, limit):
                            if exclusive:
                                raise FileExistsError(parts[-1])
                    except FileNotFoundError:
                        pass
                    tmp = ".tmp-" + uuid.uuid4().hex
                    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
                    try:
                        os.fchmod(fd, 0o600)
                        total = 0
                        while True:
                            block = stream.read(min(1024 * 1024, limit + 1 - total))
                            if not block:
                                break
                            total += len(block)
                            if total > limit:
                                raise StoreError(507, "STORAGE_FULL", "Private file exceeds its limit")
                            view = memoryview(block)
                            while view:
                                count = os.write(fd, view)
                                if count <= 0:
                                    raise OSError(errno.ENOSPC, "short write")
                                view = view[count:]
                        os.fsync(fd)
                        _regular(fd, limit)
                        with self._directory(parts[:-1]) as live:
                            a, b = os.fstat(parent), os.fstat(live)
                            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                                raise _unsafe()
                            try:
                                with self._open_blob(parts, limit):
                                    pass
                            except FileNotFoundError:
                                pass
                            if exclusive:
                                os.link(tmp, parts[-1], src_dir_fd=live,
                                        dst_dir_fd=live, follow_symlinks=False)
                                os.unlink(tmp, dir_fd=live)
                            else:
                                os.replace(tmp, parts[-1], src_dir_fd=live, dst_dir_fd=live)
                            os.fsync(live)
                    finally:
                        os.close(fd)
                        try:
                            os.unlink(tmp, dir_fd=parent)
                        except FileNotFoundError:
                            pass
            except FileExistsError:
                raise
            except OSError as exc:
                raise _io_error(exc) from None

    def _write_json(self, parts, value):
        self._write_stream(parts, io.BytesIO(_encode(value)))

    def read_record(self, kind, key):
        with self.lock:
            value = self._read_json(self._location(kind, key))
            if value is None:
                return None
            if (set(value) != {"revision", "record"}
                    or type(value["revision"]) is not int or value["revision"] < 1
                    or not isinstance(value["record"], dict)
                    or (kind == "translations" and
                        (type(value["record"].get("revision")) is not int or
                         value["record"]["revision"] != value["revision"]))):
                raise StoreError(409, "RECOVERY_REQUIRED", "Private envelope is corrupt")
            return value

    def compare_write(self, kind, key, expected_revision, record):
        with self.lock:
            location = self._location(kind, key)
            if type(expected_revision) is not int or expected_revision < 0 or not isinstance(record, dict):
                raise StoreError(400, "INVALID_REQUEST", "Invalid record or revision")
            self.acquire_owner()
            previous = self.read_record(kind, key)
            revision = previous["revision"] if previous else 0
            if revision != expected_revision:
                raise StoreError(409, "VERSION_CONFLICT", "Private record changed",
                                 {"expectedRevision": expected_revision, "actualRevision": revision})
            record = _decode(_encode(record))
            if kind == "translations":
                record["revision"] = revision + 1
                if len(record.get("history", [])) > 10:
                    raise StoreError(507, "STORAGE_FULL", "Translation history is full")
            result = {"revision": revision + 1, "record": record}
            self._write_json(location, result)
            return result

    def list_records(self, kind):
        """Startup recovery only; never exposes a filesystem listing over HTTP."""
        with self.lock:
            if kind not in KINDS:
                raise StoreError(400, "INVALID_REQUEST", "Invalid record kind")
            if kind in ("dictionary", "settings", "cache"):
                entry = self.read_record(kind, "current")
                return [] if entry is None else [entry]
            try:
                with self._directory((kind,)) as parent:
                    names = sorted(os.listdir(parent))
                    keys = []
                    for name in names:
                        if name.startswith(".tmp-"):
                            continue
                        if kind == "translations":
                            if not self._id(name, "lib"):
                                raise _unsafe()
                            with self._directory((kind, name)) as sub:
                                for filename in sorted(os.listdir(sub)):
                                    if filename.startswith(".tmp-"):
                                        continue
                                    if not filename.endswith(".json"):
                                        raise _unsafe()
                                    keys.append((name, filename[:-5]))
                        else:
                            if not name.endswith(".json"):
                                raise _unsafe()
                            keys.append(name[:-5])
                    return [self.read_record(kind, key) for key in keys]
            except FileNotFoundError:
                return []

    def acquire_owner(self):
        with self.lock:
            _supported()
            if self._owner_fd is not None:
                if self._owner_pid != os.getpid():
                    raise _unsafe()
                _regular(self._owner_fd, MAX_RECORD)
                with self._open_blob(("owner.lock",)) as live:
                    a, b = os.fstat(self._owner_fd), os.fstat(live)
                    if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                        raise _unsafe()
                return
            with self._directory(create=True) as parent:
                try:
                    fd = os.open("owner.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
                    os.fchmod(fd, 0o600)
                    os.fsync(fd)
                    os.fsync(parent)
                except FileExistsError:
                    fd = os.open("owner.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
                try:
                    _regular(fd, MAX_RECORD)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise StoreError(409, "BUSY", "Another process owns annotation state",
                                         retryable=True) from None
                    self._owner_fd = fd
                    self._owner_pid = os.getpid()
                except BaseException:
                    os.close(fd)
                    raise

    def read_credentials(self):
        with self.lock:
            return self._read_json(("translation-credentials.json",))

    def write_credentials(self, record):
        with self.lock:
            if not isinstance(record, dict):
                raise StoreError(400, "INVALID_REQUEST", "Credentials must be an object")
            self.acquire_owner()
            self.read_credentials()  # Corrupt files must not be silently overwritten.
            self._write_json(("translation-credentials.json",), record)

    def close(self):
        with self.lock:
            if self._owner_fd is not None:
                try:
                    if self._owner_pid == os.getpid():
                        fcntl.flock(self._owner_fd, fcntl.LOCK_UN)
                finally:
                    os.close(self._owner_fd)
                    self._owner_fd = None
                    self._owner_pid = None


_default_store = AnnotationStore()


def read_record(kind, key):
    return _default_store.read_record(kind, key)


def compare_write(kind, key, expected_revision, record):
    return _default_store.compare_write(kind, key, expected_revision, record)
