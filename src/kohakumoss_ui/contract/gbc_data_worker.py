"""Fixed stdin/stdout JSON entry point. Heavy dependencies live only here."""

import contextlib
import json
import sys
from pathlib import Path

# -I intentionally ignores the caller's PYTHONPATH and current directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gbc_data_common import MAX_JSON, Fault, json_value, require
from gbc_data_runtime import arm_parent_guard, check_parent


def execute(payload):
    require(isinstance(payload, dict), "invalid_request", "Request must be an object")
    action = payload.get("action")
    fields = {
        "inspect": {"action", "path", "shards"},
        "validate": {"action", "path", "shards"},
        "page": {"action", "path", "shards", "offset", "limit"},
        "graph_page": {"action", "path", "offset", "limit"},
        "dataset_media": {"action", "path", "shard", "key", "size"},
        "frozen_media": {"action", "path", "hash", "size"},
        "pack": {"action", "graphs", "imageRoot", "output", "records", "perShard", "shardBytes"},
    }
    require(isinstance(action, str) and action in fields, "invalid_request", "Unknown data action")
    require(set(payload) <= fields[action], "invalid_request", "Unexpected request fields")
    # Import after protocol validation, and never import generator/model modules.
    if action in {"dataset_media", "frozen_media"}:
        from gbc_data_media import dataset_media, frozen_media
        return {"dataset_media": dataset_media, "frozen_media": frozen_media}[action](payload)
    from gbc_data_storage import graph_page, inspect_dataset, pack, page

    if action in {"inspect", "validate"}:
        return inspect_dataset(payload, validate=action == "validate")
    return {"page": page, "graph_page": graph_page, "pack": pack}[action](payload)


def main():
    try:
        arm_parent_guard()
        raw = sys.stdin.buffer.read(MAX_JSON + 1)
        require(len(raw) <= MAX_JSON, "limit_exceeded", "Request exceeds 16 MiB")
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(json_value(raw))
        check_parent()
        reply = {"ok": True, "result": result}
    except Fault as exc:
        reply = {"ok": False, "error": {"code": exc.code, "message": str(exc)[:1500]}}
    except ImportError as exc:
        reply = {"ok": False, "error": {"code": "missing_dependency", "message": str(exc)[:1500]}}
    except OSError as exc:
        reply = {"ok": False, "error": {"code": "io_error", "message": str(exc)[:1500]}}
    except Exception as exc:  # noqa: BLE001 - fixed process protocol boundary
        # Native storage libraries also expose custom exceptions. Do not leak
        # them as traceback-only stdout or mistake failure for an empty dataset.
        reply = {"ok": False, "error": {"code": "invalid_dataset", "message": str(exc)[:1500]}}
    out = json.dumps(reply, allow_nan=False)
    if len(out.encode()) > MAX_JSON:
        out = json.dumps({"ok": False, "error": {"code": "limit_exceeded", "message": "Response exceeds 16 MiB; reduce page limit"}})
    sys.stdout.write(out + "\n")
    return 0 if reply["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
