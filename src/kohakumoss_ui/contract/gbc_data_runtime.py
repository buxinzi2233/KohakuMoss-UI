"""Worker ownership guards, armed before stdin or third-party imports."""

import ctypes
import os
import signal
import sys
import threading
import time

from gbc_data_common import Fault

_PARENT = None


def check_parent():
    """Fail closed at publication/response boundaries, including startup races."""
    if _PARENT is not None and os.getppid() != _PARENT:
        raise Fault("parent_lost", "Data worker owner exited")


def _orphan_exit():
    # Never signal a caller's shared process group (standalone diagnostic use).
    if os.name == "posix" and os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(71)


def arm_parent_guard():
    """Linux kernel death signal plus portable POSIX watchdog; no model imports."""
    global _PARENT
    raw = os.environ.get("KOHAKUMOSS_GBC_PARENT_PID")
    try:
        _PARENT = int(raw) if raw is not None else os.getppid()
    except ValueError as exc:
        raise Fault("invalid_request", "Invalid worker owner") from exc
    if _PARENT <= 1:
        raise Fault("parent_lost", "Data worker has no live owner")
    check_parent()
    if sys.platform == "linux":
        # A watchdog thread alone cannot interrupt a native extension holding the GIL.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise Fault("worker_failed", "Cannot arm Linux parent-death signal")
        check_parent()

    def watch():
        while True:
            if os.getppid() != _PARENT:
                _orphan_exit()
            time.sleep(0.05)

    threading.Thread(target=watch, name="gbc-parent-watch", daemon=True).start()
