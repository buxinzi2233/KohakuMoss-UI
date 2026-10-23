"""Filesystem locations used by the UI process."""

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"


def state_dir() -> Path:
    """UI-owned state directory; never inside the trainer's run artifacts."""
    override = os.environ.get("KOHAKUMOSS_UI_STATE", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        base = os.environ.get("XDG_STATE_HOME", "").strip()
        root = Path(base).expanduser() if base else Path.home() / ".local" / "state"
        path = root / "kohakumoss-ui"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file() -> Path:
    """JSON file holding the UI-managed child processes."""
    return state_dir() / "runs.json"


def process_log(run_name: str) -> Path:
    """stdout/stderr capture for a UI-started trainer process."""
    folder = state_dir() / "process"
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in run_name)
    return folder / f"{safe}.out"
