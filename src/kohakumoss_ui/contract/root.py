"""Discover the trainer checkout this UI is plugged into."""

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


TRAIN_SCRIPT = "scripts/train/moss.py"


def _looks_like_root(path: Path) -> bool:
    return (path / TRAIN_SCRIPT).is_file() and (path / "configs").is_dir()


def _venv_bin(root: Path, name: str) -> Path | None:
    """Find a tool shipped in the trainer's venv, on POSIX or Windows.

    POSIX layouts use ``.venv/bin/<name>`` while Windows puts console scripts in
    ``.venv/Scripts/<name>.exe``. Each branch also probes the other layout as a
    compatibility fallback for WSL checkouts or hand-made venvs.
    """
    if os.name == "nt":
        candidates = (
            root / ".venv" / "Scripts" / f"{name}.exe",
            root / ".venv" / "Scripts" / name,
            root / ".venv" / "bin" / name,
        )
    else:
        candidates = (
            root / ".venv" / "bin" / name,
            root / ".venv" / "Scripts" / f"{name}.exe",
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class TrainerRoot:
    """One resolved trainer checkout plus the interpreters it ships."""

    root: Path

    @property
    def python(self) -> Path:
        override = os.environ.get("KOHAKUMOSS_PYTHON", "").strip()
        if override:
            return Path(override).expanduser()
        venv = _venv_bin(self.root, "python")
        return venv if venv is not None else Path(sys.executable)

    @property
    def kogine(self) -> Path | None:
        venv = _venv_bin(self.root, "kogine")
        if venv is not None:
            return venv
        found = shutil.which("kogine")
        return Path(found) if found else None

    @property
    def config_bin(self) -> Path | None:
        venv = _venv_bin(self.root, "kohakumoss-config")
        if venv is not None:
            return venv
        found = shutil.which("kohakumoss-config")
        return Path(found) if found else None

    @property
    def doctor_bin(self) -> Path | None:
        venv = _venv_bin(self.root, "kohakumoss-doctor")
        if venv is not None:
            return venv
        found = shutil.which("kohakumoss-doctor")
        return Path(found) if found else None

    @property
    def runs_dir(self) -> Path:
        override = os.environ.get("KOHAKUMOSS_RUNS", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        return self.root / "runs" / "moss"

    @property
    def version(self) -> str:
        try:
            data = tomllib.loads((self.root / "pyproject.toml").read_text("utf-8"))
            return str(data.get("project", {}).get("version", "unknown"))
        except (OSError, tomllib.TOMLDecodeError):
            return "unknown"

    def info(self) -> dict:
        """JSON-safe description of the checkout for the UI."""
        return {
            "root": str(self.root),
            "name": self.root.name,
            "version": self.version,
            "python": str(self.python),
            "kogine": str(self.kogine) if self.kogine else None,
            "config_tool": str(self.config_bin) if self.config_bin else None,
            "doctor_tool": str(self.doctor_bin) if self.doctor_bin else None,
            "runs_dir": str(self.runs_dir),
            "train_script": TRAIN_SCRIPT,
        }


def discover(explicit: str | None = None) -> TrainerRoot:
    """Find the trainer root from a flag, env, cwd, or a sibling checkout."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_root = os.environ.get("KOHAKUMOSS_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())

    cwd = Path.cwd()
    candidates.append(cwd)
    candidates.extend(cwd.parents)

    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    candidates.append(cwd.parent / "KohakuMossTrainer")

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _looks_like_root(resolved):
            return TrainerRoot(resolved)

    raise RuntimeError(
        "trainer root not found; pass --root /path/to/KohakuMossTrainer "
        "or set KOHAKUMOSS_ROOT"
    )
