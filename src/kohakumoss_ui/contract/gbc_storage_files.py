"""Dataset-only streaming I/O and metadata fences, independent of image limits.

Registered files are immutable. Pagination checks a bounded inventory of stat
identities, never hashes file contents or discovers a new baseline. Explicit
validation and trusted draft resolution stream contents. No archive extraction.
"""

import hashlib
import os
import stat
from pathlib import Path

from .gbc_paths import safe_path
from .gbc_schema import GbcError, fail

MAX_TOTAL = 20 * 1024**3
MAX_FILE = 8 * 1024**3
MAX_ENTRIES = 10000
CHUNK = 1024 * 1024


def signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def inspect(path, directory):
    safe_path(path, directory=directory)
    info = os.stat(path, follow_symlinks=False)
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        fail("UNSAFE_PATH", "Dataset contains a link or special file")
    return signature(info)


def inventory(root):
    """Bounded metadata-only discovery, used at registration, not on each page."""
    root = safe_path(root)[0]
    entries = {}
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        relative = Path(base).relative_to(root)
        if len(relative.parts) > 16:
            fail("LIMIT_EXCEEDED", "Dataset directory depth exceeds 16", 413)
        entries[str(relative)] = {"directory": True, "signature": inspect(Path(base), True)}
        for name in dirs:
            inspect(Path(base) / name, True)
        for name in sorted(files):
            path = Path(base) / name
            identity = inspect(path, False)
            total += identity[4]
            if identity[4] > MAX_FILE or total > MAX_TOTAL:
                fail("LIMIT_EXCEEDED", "Dataset exceeds 8 GiB per-file or 20 GiB total service limit", 413)
            entries[str(path.relative_to(root))] = {"directory": False, "signature": identity}
        if len(entries) + len(dirs) > MAX_ENTRIES:
            fail("LIMIT_EXCEEDED", "Dataset exceeds 10000 directory/file entries", 413)
    guard(root, entries)
    return entries


def guard(root, entries):
    """No walking/reading bytes: root, directories and files retain identity.

    Directory mtime/ctime fences additions/removals; file ctime catches ordinary
    same-size edits even if mtime is restored. This is not a hostile same-uid
    filesystem sandbox. Check before and after the read-only worker operation.
    """
    if not isinstance(entries, dict) or "." not in entries or len(entries) > MAX_ENTRIES:
        fail("SOURCE_CHANGED", "Dataset needs explicit validation to establish its identity fence", 409)
    try:
        for relative, entry in entries.items():
            part = Path(relative)
            if part.is_absolute() or ".." in part.parts:
                fail("SOURCE_CHANGED", "Invalid stored dataset identity", 409)
            if inspect(Path(root) / part, entry["directory"]) != entry["signature"]:
                fail("SOURCE_CHANGED", "Dataset changed since validation", 409)
    except (OSError, GbcError) as exc:
        if isinstance(exc, GbcError) and exc.code == "SOURCE_CHANGED":
            raise
        raise GbcError(409, "SOURCE_CHANGED", "Dataset path or identity changed") from None


def _stream(path, expected, target=None):
    safe_path(path, directory=False)
    source = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    destination = None
    try:
        if signature(os.fstat(source)) != expected:
            fail("SOURCE_CHANGED", "Dataset file changed before streaming", 409)
        if target is not None:
            safe_path(target.parent)
            destination = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        hashed = hashlib.sha256()
        count = 0
        while True:
            block = os.read(source, CHUNK)
            if not block:
                break
            count += len(block)
            if count > expected[4] or count > MAX_FILE:
                fail("SOURCE_CHANGED", "Dataset file grew during streaming", 409)
            hashed.update(block)
            if destination is not None:
                view = memoryview(block)
                while view:
                    written = os.write(destination, view)
                    if written <= 0:
                        raise OSError("Short dataset write")
                    view = view[written:]
        if count != expected[4] or signature(os.fstat(source)) != expected or inspect(path, False) != expected:
            fail("SOURCE_CHANGED", "Dataset file changed during streaming", 409)
        copied = None
        if destination is not None:
            os.fsync(destination)
            copied = signature(os.fstat(destination))
        return {"hash": "sha256:" + hashed.hexdigest(), "bytes": count}, copied
    finally:
        os.close(source)
        if destination is not None:
            os.close(destination)


def snapshot(root):
    """Full content hash, at most one 1MiB payload block retained in memory."""
    root = Path(root)
    entries = inventory(root)
    manifest = []
    for relative, entry in sorted(entries.items()):
        if not entry["directory"]:
            content, _ = _stream(root / relative, entry["signature"])
            manifest.append({"path": relative, **content})
    guard(root, entries)
    return manifest, entries


def copy_dataset(source, destination):
    """Exclusive streaming copy; never unpacks ZIP/DB files or overwrites output."""
    source, destination = Path(source), Path(destination)
    entries = inventory(source)
    safe_path(destination.parent)
    destination.mkdir(mode=0o700)
    copied = {}
    manifest = []
    for relative, entry in sorted(entries.items(), key=lambda item: (len(Path(item[0]).parts), item[0])):
        if entry["directory"] and relative != ".":
            safe_path((destination / relative).parent)
            (destination / relative).mkdir(mode=0o700)
    for relative, entry in sorted(entries.items()):
        if entry["directory"]:
            continue
        content, identity = _stream(source / relative, entry["signature"], destination / relative)
        manifest.append({"path": relative, **content})
        copied[relative] = {"directory": False, "signature": identity}
    # Directory timestamps are captured only after this operation's own writes.
    for relative, entry in entries.items():
        if entry["directory"]:
            copied[relative] = {"directory": True, "signature": inspect(destination / relative, True)}
    guard(source, entries)
    guard(destination, copied)
    return manifest, copied
