"""List and resolve kogine config files through the trainer's interpreter."""

import ast
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .root import TrainerRoot


_LOAD_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


LOAD_CODE = """
import json, sys
from kohakuengine import load_config_file

cfg = load_config_file(sys.argv[1])

def conv(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [conv(item) for item in value]
    if isinstance(value, dict):
        return {str(key): conv(item) for key, item in value.items()}
    return repr(value)

print(json.dumps({key: conv(value) for key, value in cfg.globals_dict.items()}, default=str))
"""


def _docstring(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text("utf-8"))
    except (OSError, SyntaxError):
        return ""
    text = ast.get_docstring(tree) or ""
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


def list_configs(root: TrainerRoot) -> list[dict]:
    """Every Python config under configs/, tagged by its group."""
    configs_dir = root.root / "configs"
    items: list[dict] = []
    if not configs_dir.is_dir():
        return items
    for path in sorted(configs_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root.root).as_posix()
        parts = path.relative_to(configs_dir).parts
        group = parts[1] if len(parts) >= 3 else (parts[0] if parts else "")
        items.append(
            {
                "path": rel,
                "name": path.stem,
                "group": group,
                "dir": path.parent.relative_to(configs_dir).as_posix(),
                "doc": _docstring(path),
                "mtime": path.stat().st_mtime,
            }
        )
    return items


def load_config(root: TrainerRoot, relpath: str, max_age: float = 5.0) -> dict:
    """Resolve a config's knobs with kohakuengine, cached by mtime."""
    path = (root.root / relpath).resolve()
    if not path.is_file():
        raise FileNotFoundError(relpath)
    mtime = path.stat().st_mtime
    with _CACHE_LOCK:
        cached = _LOAD_CACHE.get(str(path))
        if cached and cached[0] == mtime and time.time() - cached[0] < max_age:
            return cached[1] | {"_cached": True}

    result = subprocess.run(
        [str(root.python), "-c", LOAD_CODE, str(path)],
        cwd=root.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-2000:])
    values = json.loads(result.stdout.strip().splitlines()[-1])
    with _CACHE_LOCK:
        _LOAD_CACHE[str(path)] = (mtime, values)
    return values


def check_config(root: TrainerRoot, relpath: str) -> dict:
    """Run the trainer's own config checker; slow because it imports torch."""
    path = (root.root / relpath).resolve()
    if not path.is_file():
        raise FileNotFoundError(relpath)
    tool = root.config_bin
    if tool is None:
        return {"ok": False, "output": "kohakumoss-config not found"}
    result = subprocess.run(
        [str(tool), "check", str(path)],
        cwd=root.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=300,
    )
    output = (result.stdout + result.stderr).strip()
    return {"ok": result.returncode == 0, "output": output[-4000:]}
