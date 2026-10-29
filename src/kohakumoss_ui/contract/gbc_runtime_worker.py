"""Fixed stdin-JSON supervisor. Only canonical GBC CLI verbs are executable.

Runs in the explicitly trusted Python environment. Parent loss and configured
wall timeout kill this supervisor's own process group, never a discovered PID.
"""

import ctypes
import json
import os
import resource
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path


def group_members():
    members = []
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(path.parent.name)
            fields = path.read_text().rsplit(")", 1)[1].split()
            if pid != os.getpid() and int(fields[2]) == os.getpgrp() and fields[0] != "Z":
                members.append(pid)
        except (OSError, ValueError, IndexError):
            continue
    return members


def main():
    request = json.loads(sys.stdin.buffer.read(65537))
    if set(request) != {"action", "config", "timeout", "parent", "output"}:
        raise ValueError("invalid supervisor request")
    action = request["action"]
    if action not in {"run", "check", "doctor", "version"}:
        raise ValueError("invalid action")
    if type(request["timeout"]) is not int or not 1 <= request["timeout"] <= 86400:
        raise ValueError("invalid timeout")
    config = request["config"]
    if action in {"run", "check"} and (not isinstance(config, str) or not Path(config).is_absolute()):
        raise ValueError("managed config required")
    # Adopt grandchildren, so normal cancellation does not leave zombies behind.
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # Linux PR_SET_CHILD_SUBREAPER
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    args = [sys.executable, "-m", "kohakugbc.cli.main"]
    args += ["--version"] if action == "version" else [action]
    if action in {"run", "check"}:
        args.append(config)
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV", "SYSTEMROOT"}}
    env.update(PYTHONUNBUFFERED="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    if stopping:
        return 143
    resource.setrlimit(resource.RLIMIT_FSIZE, (268435456, 268435456))
    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, shell=False, env=env)
    if stopping:
        os.killpg(os.getpgrp(), signal.SIGTERM)
    poller = selectors.DefaultSelector()
    poller.register(child.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + request["timeout"]
    next_quota_check = 0
    try:
        while True:
            if os.getppid() != request["parent"] or time.monotonic() >= deadline:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            if time.monotonic() >= next_quota_check:
                next_quota_check = time.monotonic() + 1
                total, count = 0, 0
                for base, dirs, files in os.walk(request["output"], followlinks=False):
                    count += len(dirs) + len(files)
                    for name in files:
                        try:
                            total += os.lstat(os.path.join(base, name)).st_size
                        except FileNotFoundError:
                            pass
                    if total > 268435456 or count > 10000:
                        os.killpg(os.getpgrp(), signal.SIGKILL)
            for key, _ in poller.select(0.1):
                block = os.read(key.fd, 8192)
                if not block:
                    poller.unregister(key.fileobj)
                else:
                    try:
                        sys.stdout.buffer.write(block)
                        sys.stdout.buffer.flush()
                    except BrokenPipeError:
                        os.killpg(os.getpgrp(), signal.SIGKILL)
            code = child.poll()
            if code is not None:
                # Reap orphaned descendants, but never claim a live group stopped.
                try:
                    while os.waitpid(-1, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
                if not group_members() and not poller.get_map():
                    return code if code >= 0 else 128 - code
    finally:
        poller.close()
        child.stdout.close()


if __name__ == "__main__":
    sys.exit(main())
