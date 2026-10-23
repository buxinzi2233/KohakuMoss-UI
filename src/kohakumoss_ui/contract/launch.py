"""Start and stop trainer processes through the kogine CLI."""

import os
import signal
import subprocess
import time
from pathlib import Path

from .. import paths
from . import configs, state
from .root import TRAIN_SCRIPT, TrainerRoot


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


def start(
    root: TrainerRoot,
    config_rel: str,
    overrides: dict | None = None,
    run_name: str | None = None,
) -> dict:
    """Spawn `kogine run` in its own process group and record its pid."""
    overrides = dict(overrides or {})
    if run_name:
        overrides["RUN_NAME"] = run_name
    config_path = (root.root / config_rel).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_rel)

    name = resolve_run_name(root, config_rel, overrides)
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("RUN_NAME must be a single directory name")
    cmd = command(root, config_rel, overrides)
    log_path = paths.process_log(name)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    with log_path.open("ab") as sink:
        sink.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)} ===\n".encode())
        sink.flush()
        creation = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            cmd,
            cwd=root.root,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=creation,
            env=env,
        )

    state.register(name, process.pid, config_rel, cmd, str(log_path))
    time.sleep(0.15)
    return {
        "name": name,
        "pid": process.pid,
        "alive": state.pid_alive(process.pid),
        "command": cmd,
        "log": str(log_path),
    }


def stop(name: str, force: bool = False) -> dict:
    """SIGTERM the process group; the trainer unwinds through SystemExit(143)."""
    item = state.status(name)
    if item is None:
        return {"stopped": False, "reason": "not managed by this UI"}
    pid = int(item.get("pid", 0))
    if not item.get("alive"):
        return {"stopped": False, "reason": "process is not alive", "pid": pid}

    sig = signal.SIGKILL if force else signal.SIGTERM
    if os.name == "nt":
        flags = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            flags.append("/F")
        try:
            subprocess.run(flags, capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return {"stopped": False, "reason": "taskkill failed", "pid": pid}
    else:
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, sig)
            except OSError:
                return {"stopped": False, "reason": "signal failed", "pid": pid}

    deadline = time.time() + (2.0 if force else 15.0)
    while time.time() < deadline:
        if not state.pid_alive(pid):
            return {"stopped": True, "pid": pid, "signal": sig.name}
        time.sleep(0.2)
    return {"stopped": False, "pid": pid, "signal": sig.name, "reason": "still running"}
