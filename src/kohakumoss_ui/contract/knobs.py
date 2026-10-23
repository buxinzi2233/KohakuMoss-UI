"""Read trainer knobs from the script's AST without importing the package."""

import ast
import io
import json
import re
import tokenize
from pathlib import Path


SECTION_RE = re.compile(r"^\s*#\s*={3,}\s*(.+?)\s*={3,}\s*$")
ASSIGN_NODES = (ast.Assign, ast.AnnAssign)


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return None


def _comments(source: str) -> dict[int, str]:
    found: dict[int, str] = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                found[token.start[0]] = token.string.lstrip("#").strip()
    except (tokenize.TokenError, IndentationError):
        pass
    return found


def _target_name(node) -> str | None:
    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def scan_script(path: Path) -> dict:
    """Return sections and module-level knob defaults from one trainer script."""
    source = path.read_text("utf-8")
    lines = source.splitlines()
    comments = _comments(source)
    tree = ast.parse(source, filename=str(path))

    sections: list[dict] = []
    current = {"title": "GENERAL", "knobs": []}
    sections.append(current)
    cursor = 1

    for node in tree.body:
        if not isinstance(node, ASSIGN_NODES):
            continue
        name = _target_name(node)
        if not name or name.startswith("__"):
            continue

        for line_no in range(cursor, node.lineno):
            match = SECTION_RE.match(lines[line_no - 1])
            if match:
                current = {"title": match.group(1).strip(), "knobs": []}
                sections.append(current)
        cursor = max(cursor, node.lineno)

        try:
            default = ast.unparse(node.value).strip()
        except Exception:
            default = ""
        try:
            literal = ast.literal_eval(node.value)
        except Exception:
            literal = None

        annotation = None
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            try:
                annotation = ast.unparse(node.annotation).strip()
            except Exception:
                annotation = None

        default_value = _jsonable(literal)
        if default_value is None and literal is not None:
            default_value = repr(literal)

        current["knobs"].append(
            {
                "name": name,
                "default": default,
                "value": default_value,
                "literal": literal is not None,
                "type": annotation,
                "comment": comments.get(node.lineno, ""),
                "line": node.lineno,
            }
        )

    sections = [section for section in sections if section["knobs"]]
    return {
        "script": str(path),
        "sections": sections,
        "count": sum(len(section["knobs"]) for section in sections),
        "names": [knob["name"] for section in sections for knob in section["knobs"]],
    }


def dump_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2)
