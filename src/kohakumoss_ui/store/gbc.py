"""GBC private records; reuse proven no-follow atomic storage primitives.

The nested gbc/annotations directory is private implementation detail, not a
shared translation namespace. Cooperating processes hold a lifetime owner lock.
"""

import io
import json
from pathlib import Path

from .annotations import AnnotationStore


class GbcStore(AnnotationStore):
    def __init__(self, state=None):
        super().__init__(state if state is not None else AnnotationStore()._state_path())

    def _state_path(self):
        return super()._state_path() / "gbc"

    @property
    def directory(self):
        with self._directory(create=True):
            return self._state_path() / "annotations"

    def read(self):
        return self._read_json(("service.json",))

    def write(self, value):
        self._write_json(("service.json",), value)

    def blob(self, name, content):
        self._write_stream((name,), io.BytesIO(content), limit=256 * 1024 * 1024, exclusive=True)
        return self.directory / name

    def workdir(self, name):
        with self._directory((name,), create=True):
            return self.directory / name

    def bytes(self, name, content):
        self._write_stream((name,), io.BytesIO(content))

    def manifest(self, name, value):
        return self.blob(name, json.dumps(value, ensure_ascii=True).encode())


def state_path():
    """Locate without following or creating caller-controlled directories."""
    return Path(AnnotationStore()._state_path())
