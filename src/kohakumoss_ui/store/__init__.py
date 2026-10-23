"""UI-owned state documents kept outside the trainer's run directories."""

from .. import paths


def state_path(name: str):
    """Absolute path of one state file; the state directory is created."""
    return paths.state_dir() / name
