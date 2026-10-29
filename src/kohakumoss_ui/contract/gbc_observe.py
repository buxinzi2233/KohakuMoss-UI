"""Count only complete bounded output records mapped to frozen input identities.

These are file observations, not semantic validation or a model progress meter.
"""

from pathlib import Path

from .annotations import strict_json
from .gbc_paths import read_file
from .gbc_schema import GbcError


def observe(output, records, terminal=False):
    counts = {"total": len(records), "ok": 0, "failed": 0, "observedRecords": 0,
              "invalidRecords": 0, "duplicateRecords": 0, "validated": None}
    images = {row["image"]: row["key"] for row in records}
    keys = {row["meta"]["sourceKey"]: row["key"] for row in records}
    ok, failed = set(), set()
    for filename, successful in (("graphs.jsonl", True), ("graphs.failures.jsonl", False)):
        target = Path(output) / filename
        if not target.exists():
            continue
        try:
            raw = read_file(target, 16 * 1024 * 1024)
        except GbcError as exc:
            counts["observationError"] = exc.code
            continue
        for index, line in enumerate(raw.splitlines(keepends=True)):
            if index >= 10000 or len(line) > 2 * 1024 * 1024:
                counts["observationError"] = "LIMIT_EXCEEDED"
                break
            if not line.endswith(b"\n"):
                if terminal:
                    counts["invalidRecords"] += 1
                break
            if not line.strip():
                continue
            counts["observedRecords"] += 1
            try:
                record = strict_json(line)
                if not isinstance(record, dict):
                    raise TypeError("object required")
                key = images.get(record.get("img_path")) if successful else keys.get(record.get("key"))
                if key is None or (successful and not isinstance(record.get("vertices"), list)):
                    raise ValueError("unmapped output")
                if key in ok or key in failed:
                    counts["duplicateRecords"] += 1
                    continue
                (ok if successful else failed).add(key)
            except (ValueError, TypeError, KeyError):
                counts["invalidRecords"] += 1
    counts.update(ok=len(ok), failed=len(failed))
    return counts
