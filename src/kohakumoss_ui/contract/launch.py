"""Start and stop trainer processes through the kogine CLI."""

import os
import signal
import subprocess
import threading
import time

from .. import paths
from . import configs, state
from .root import TRAIN_SCRIPT, TrainerRoot

_RESERVED_WINDOWS_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{index}" for index in range(1, 10)]
    + [f"LPT{index}" for index in range(1, 10)]
)
_INVALID_NAME_CHARS = frozenset('<>:"|?*')


def _windows_creation_flags(new_group: bool) -> int:
    """Popen/taskkill process creation flags, or 0 outside Windows.

    The numeric fallbacks keep the Windows branch importable and testable on
    POSIX, where subprocess does not define these constants at all.
    """
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    if new_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    return flags


def _validate_run_name(name: str) -> None:
    """Reject RUN_NAME values that cannot be a portable single directory name."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("RUN_NAME must be a single directory name")
    # Illegal on Windows and hostile to other tooling too, so checked on every
    # platform: one config must stay usable from POSIX and Windows checkouts.
    if any(char in _INVALID_NAME_CHARS or ord(char) < 32 for char in name):
        raise ValueError('RUN_NAME must not contain <>:"|?* or control characters')
    if os.name == "nt":
        # Windows resolves reserved device names even with an extension
        # ("con.txt") and silently trims trailing dots/spaces ("run." -> "run").
        # Both are rejected here only; POSIX keeps them legal.
        if name.split(".")[0].rstrip(" ").upper() in _RESERVED_WINDOWS_NAMES:
            raise ValueError("RUN_NAME must not be a reserved Windows device name")
        if name != name.rstrip(" ."):
            raise ValueError("RUN_NAME must not end with a dot or a space")


def resolve_run_name(root: TrainerRoot, config_rel: str, overrides: dict) -> str:
    """RUN_NAME from the override or the resolved config, else the trainer default."""
    if overrides.get("RUN_NAME"):
        return str(overrides["RUN_NAME"])
    try:
        values = configs.load_config(root, config_rel)
        return str(values.get("RUN_NAME") or "run")
    except Exception:
        return "run"


def command(root: TrainerRoot, config_rel: str, overrides: dict) -> list[str]:
    if root.kogine is None:
        raise RuntimeError("kogine not found in the trainer environment")
    cmd = [str(root.kogine), "run", TRAIN_SCRIPT, "--config", config_rel]
    for key, value in overrides.items():
        cmd.extend(["--set", f"{key}={value}"])
    return cmd


class _UnconfirmedSpawn(RuntimeError):
    """A child exists but teardown was not proven; its lease must stay fenced."""


def _cleanup_owned(process, expected):
    """Retire only this Popen's verified new-session group, or fail closed."""
    from .gbc_process import group_alive
    try:
        if process.poll() is None:
            if not state.identity_matches(process.pid, expected):
                return False
            os.killpg(expected["group"], signal.SIGKILL)
        process.wait(timeout=5)
        return not group_alive(process.pid)
    except (OSError, subprocess.TimeoutExpired):
        return False


def start(
    root: TrainerRoot,
    config_rel: str = "",
    overrides: dict | None = None,
    run_name: str | None = None,
    *,
    draft_ref: dict | None = None,
) -> dict:
    """Shared manual/queue entry: guarded config and atomic generation lease."""
    if draft_ref is not None:
        if config_rel or overrides or run_name:
            raise ValueError("draftRef cannot be combined with legacy config or overrides")
        try:
            from .training_drafts import resolve_for_launch
        except ImportError:
            from .gbc_schema import GbcError
            raise GbcError(409, "UNSUPPORTED_CAPABILITY", "Training draft launch adapter is not available") from None
        from .training_drafts import DraftError
        try:
            config_rel = str(resolve_for_launch(root, draft_ref))
        except DraftError as exc:
            from .gbc_schema import GbcError
            raise GbcError(409, exc.code, str(exc)) from None
    else:
        target = (root.root / config_rel).resolve()
        allowed = (root.root / "configs").resolve()
        if not config_rel or allowed not in target.parents:
            raise ValueError("config path must stay under configs/")
    if os.name == "nt":
        result, _ = _start_unlocked(root, config_rel, overrides, run_name)
        return result
    from .gbc_lease import Lease
    from .gbc_process import group_alive
    lease = Lease("training")
    process = None
    expected = None
    try:
        result, process = _start_unlocked(root, config_rel, overrides, run_name)
        expected = state.process_identity(process.pid)
        lease.bind(process.pid, expected)
    except BaseException as exc:
        if isinstance(exc, _UnconfirmedSpawn):
            lease.abandon()
        elif process is None or _cleanup_owned(process, expected):
            lease.release()
        else:
            lease.abandon()
        raise

    def release_when_reaped():
        process.wait()
        if group_alive(process.pid):
            lease.abandon()
        else:
            lease.release()

    threading.Thread(target=release_when_reaped, name="training-lease", daemon=True).start()
    return result


def _start_unlocked(
    root: TrainerRoot,
    config_rel: str,
    overrides: dict | None = None,
    run_name: str | None = None,
) -> tuple:
    """Spawn `kogine run` while the caller owns the shared resource lease."""
    overrides = dict(overrides or {})
    if run_name:
        overrides["RUN_NAME"] = run_name
    config_path = (root.root / config_rel).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_rel)

    name = resolve_run_name(root, config_rel, overrides)
    _validate_run_name(name)
    cmd = command(root, config_rel, overrides)
    log_path = paths.process_log(name)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    with log_path.open("ab") as sink:
        sink.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)} ===\n".encode())
        sink.flush()
        # Windows: own process group (a console Ctrl+C must not reach the
        # trainer) and no console window for the child.
        creation = _windows_creation_flags(new_group=True)
        process = subprocess.Popen(
            cmd,
            cwd=root.root,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=creation,
            env=env,
        )

    expected = None
    try:
        expected = state.process_identity(process.pid)
        state.register(name, process.pid, config_rel, cmd, str(log_path))
        time.sleep(0.15)
        return {
            "name": name,
            "pid": process.pid,
            "alive": state.pid_alive(process.pid),
            "command": cmd,
            "log": str(log_path),
        }, process
    except BaseException as exc:
        # Preserve the outer fence if an already-created group cannot be reaped.
        if os.name == "nt":
            _taskkill(process.pid, force=True)
            process.wait(timeout=5)
        elif not _cleanup_owned(process, expected):
            raise _UnconfirmedSpawn("New process cleanup is unconfirmed; recovery required") from exc
        raise


def _taskkill(pid: int, force: bool) -> bool:
    """Run taskkill on a Windows pid; True when it reported success."""
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            timeout=15,
            creationflags=_windows_creation_flags(new_group=False),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _await_exit(pid: int, deadline: float, signal_name: str) -> dict:
    """Poll pid_alive until the deadline and report how the stop ended."""
    while time.time() < deadline:
        if not state.pid_alive(pid):
            return {"stopped": True, "pid": pid, "signal": signal_name}
        time.sleep(0.2)
    return {"stopped": False, "pid": pid, "signal": signal_name, "reason": "still running"}


def _stop_windows(pid: int, force: bool) -> dict:
    """Stop a Windows process with taskkill; there is no signal.SIGKILL here.

    Without /F taskkill asks the trainer to unwind on its own. That is only
    worth waiting for when the request actually reached it, so a failed polite
    attempt escalates to /F immediately instead of burning the grace period.
    """
    if not force and _taskkill(pid, force=False):
        result = _await_exit(pid, time.time() + 15.0, "taskkill")
        if result["stopped"]:
            return result
    # Forced path: the caller asked for it, the polite request never landed, or
    # the trainer survived the grace period.
    if not _taskkill(pid, force=True):
        return {"stopped": False, "pid": pid, "signal": "taskkill /F", "reason": "taskkill failed"}
    return _await_exit(pid, time.time() + 2.0, "taskkill /F")


def stop(name: str, force: bool = False) -> dict:
    """Stop a managed run: SIGTERM the group on POSIX, taskkill on Windows."""
    item = state.status(name)
    if item is None:
        return {"stopped": False, "reason": "not managed by this UI"}
    pid = int(item.get("pid", 0))
    # Keep tested Windows taskkill behavior; no Linux identity guarantee there.
    if os.name == "nt":
        if not item.get("alive"):
            return {"stopped": False, "reason": "process is not alive", "pid": pid}
        return _stop_windows(pid, force)

    expected = item.get("identity")
    recovery = {"stopped": False, "pid": pid, "code": "RECOVERY_REQUIRED",
                "reason": "Process identity missing or changed; manual recovery required"}
    if not expected or item.get("recoveryRequired"):
        return recovery
    if not item.get("alive"):
        return {"stopped": False, "reason": "process is not alive", "pid": pid}
    # Recheck immediately before signaling the ORIGINAL group, never getpgid()
    # on a reused PID and never fall back to an unverified kill(pid).
    if not state.identity_matches(pid, expected):
        return recovery
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(expected["group"], sig)
    except OSError:
        return {"stopped": False, "reason": "signal failed", "pid": pid}
    return _await_exit(pid, time.time() + (2.0 if force else 15.0), sig.name)
