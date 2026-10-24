"""Persistent registry of trainer processes started by this UI."""

import ctypes
import functools
import json
import os
import threading
import time
from pathlib import Path

from .. import paths


_LOCK = threading.Lock()

# Win32 constants used by the module-level liveness probe.
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_ERROR_ACCESS_DENIED = 5


def _read() -> dict:
    try:
        data = json.loads(paths.state_file().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"runs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
        return {"runs": {}}
    return data


def _write(data: dict) -> None:
    target = paths.state_file()
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(tmp, target)


def _alive_posix(pid: int) -> bool:
    """True when the pid exists and is not a zombie."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    proc = Path("/proc") / str(pid)
    if proc.is_dir():
        try:
            stat = (proc / "stat").read_text("utf-8")
            if stat.rsplit(")", 1)[-1].strip().startswith("Z"):
                return False
        except OSError:
            pass
    return True


def _last_error() -> int:
    """Win32 error of the calling thread; ctypes exposes it on Windows only."""
    get_error = getattr(ctypes, "get_last_error", None)
    return int(get_error()) if get_error is not None else 0


@functools.lru_cache(maxsize=1)
def _kernel32():
    """kernel32 with the Win32 signatures used by the probe (loaded once)."""
    from ctypes import wintypes

    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    lib.OpenProcess.restype = wintypes.HANDLE
    lib.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    lib.WaitForSingleObject.restype = wintypes.DWORD
    lib.CloseHandle.argtypes = [wintypes.HANDLE]
    lib.CloseHandle.restype = wintypes.BOOL
    return lib


def _alive_windows(pid: int) -> bool:
    """True when the pid is a running Windows process.

    ``os.kill(pid, 0)`` must not be used here: on Windows CPython turns every
    signal except CTRL_C_EVENT / CTRL_BREAK_EVENT into TerminateProcess, so the
    liveness check itself would kill the process (launch.start probes it 0.15 s
    after spawning). OpenProcess + WaitForSingleObject only observes the handle.
    """
    if pid <= 0:
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        # ERROR_INVALID_PARAMETER means "no such pid"; ERROR_ACCESS_DENIED means
        # the process exists but belongs to another user, which POSIX also
        # reports as alive through PermissionError.
        return _last_error() == _ERROR_ACCESS_DENIED
    try:
        code = kernel32.WaitForSingleObject(handle, 0)
    finally:
        kernel32.CloseHandle(handle)
    if code == _WAIT_TIMEOUT:
        return True
    if code == _WAIT_OBJECT_0:
        return False
    # WAIT_FAILED and friends: an unreadable handle is not evidence of life.
    return False


def pid_alive(pid: int) -> bool:
    """True when the pid exists (and, on POSIX, is not a zombie)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _alive_windows(pid)
    return _alive_posix(pid)


def register(name: str, pid: int, config: str, command: list[str], log_path: str) -> None:
    """Record a child process so its state survives a UI restart."""
    with _LOCK:
        data = _read()
        data["runs"][name] = {
            "pid": pid,
            "config": config,
            "command": command,
            "log_path": log_path,
            "started_at": time.time(),
        }
        _write(data)


def remove(name: str) -> None:
    with _LOCK:
        data = _read()
        if data["runs"].pop(name, None) is not None:
            _write(data)


def entry(name: str) -> dict | None:
    with _LOCK:
        return _read()["runs"].get(name)


def all_entries() -> dict:
    with _LOCK:
        return dict(_read()["runs"])


def status(name: str) -> dict | None:
    """Return a managed entry plus a live flag, or None when unmanaged."""
    item = entry(name)
    if item is None:
        return None
    result = dict(item)
    result["alive"] = pid_alive(int(item.get("pid", 0)))
    return result
