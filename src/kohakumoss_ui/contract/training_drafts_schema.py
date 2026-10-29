"""Strict JSON-only draft input. No trainer imports in the UI process."""

import json
import math
import re


class DraftError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def fail(message, code="INVALID_REQUEST"):
    raise DraftError(code, message)


def json_value(value, depth=0):
    if depth > 32:
        fail("JSON nesting limit exceeded")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            fail("Invalid Unicode")
        if len(value) > 262144:
            fail("String limit exceeded")
        return value
    if type(value) is list:
        return [json_value(v, depth + 1) for v in value]
    if type(value) is dict and all(type(k) is str for k in value):
        return {k: json_value(v, depth + 1) for k, v in value.items()}
    fail("Expected finite JSON data, not Python objects")


def canonical(value):
    data = json.dumps(
        json_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    if len(data) > 4 * 1024 * 1024:
        fail("JSON size limit exceeded", "LIMIT_EXCEEDED")
    return data


def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                fail("Duplicate JSON key")
            out[key] = value
        return out

    try:
        return json_value(
            json.loads(
                data,
                object_pairs_hook=pairs,
                parse_constant=lambda _: fail("Non-finite JSON"),
            )
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DraftError("WORKER_PROTOCOL", "Invalid JSON") from exc


def obj(value, allowed, required=()):
    if (
        type(value) is not dict
        or set(value) - set(allowed)
        or set(required) - set(value)
    ):
        fail("Unknown or missing fields: " + ", ".join(sorted(allowed)))
    return value


def text(value, label, nonempty=True):
    if type(value) is not str or (nonempty and not value) or "\x00" in value:
        fail(label + " must be a string")
    return value


def number(value, label, low=0, high=None):
    if type(value) not in (float, int) or not math.isfinite(value):
        fail(label + " must be a finite number")
    if value < low or (high is not None and value > high):
        fail(label + " out of range")
    return value


def integer(value, label, low=0, high=2**53 - 1):
    if type(value) is not int:
        fail(label + " must be an integer")
    return number(value, label, low, high)


def rate(value, label):
    """A drop or keep rate: a number, or [lo, hi] drawn per caption by the trainer."""
    if type(value) is list:
        if len(value) != 2:
            fail(label + " must be a number or a [lo, hi] pair")
        low, high = (number(bound, label, 0, 1) for bound in value)
        if low > high:
            fail(label + " range is reversed")
        return value
    return number(value, label, 0, 1)


def strings(value, label, nonempty=False):
    if type(value) is not list or (nonempty and not value):
        fail(label + " must be a list")
    for v in value:
        text(v, label)
    return value


def caption(value):
    obj(
        value,
        {
            "template",
            "formats",
            "meta",
            "tags",
            "nl",
            "graph",
            "list_sep",
            "section_sep",
            "text_sep",
            "underscores",
            "fit",
            "prefix",
            "suffix",
        },
        {"template", "formats"},
    )
    if value["template"] != "gbc":
        fail("Only the registered gbc caption template is supported")
    meta = value.get("meta", {})
    if type(meta) is not dict:
        fail("meta must be an object (not metadata)")
    for name, rule in meta.items():
        text(name, "meta section")
        if name in {"tags", "nl", "graph"}:
            fail("Metadata section shadows content")
        obj(rule, {"field", "bins", "ranges", "map", "drop"}, {"field"})
        if not re.fullmatch(
            r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", text(rule["field"], "field")
        ):
            fail("Invalid metadata field")
        rate(rule.get("drop", 0), "drop")
        for key, n in (("bins", 2), ("ranges", 3)):
            rows = rule.get(key, [])
            if type(rows) is not list:
                fail(key + " must be a list")
            for row in rows:
                if type(row) is not list or len(row) != n:
                    fail("Invalid " + key)
                for bound in row[:-1]:
                    number(bound, key, -1e100, 1e100)
                if n == 3 and row[0] > row[1]:
                    fail("Invalid range")
                if type(row[-1]) is list:
                    strings(row[-1], key)
                else:
                    text(row[-1], key, False)
        mapping = rule.get("map", {})
        if type(mapping) is not dict:
            fail("map must be an object")
        for mapped in mapping.values():
            if type(mapped) is list:
                strings(mapped, "map")
            else:
                text(mapped, "map", False)
    formats = value["formats"]
    if type(formats) is not list or not formats or len(formats) > 128:
        fail("formats must be nonempty")
    sections = {"graph", "nl", "tags"} | set(meta)
    for fmt in formats:
        obj(
            fmt,
            {"weight", "sections", "requires", "drop"},
            {"weight", "sections"},
        )
        number(fmt["weight"], "format weight", 1e-12, 1e12)
        if set(strings(fmt["sections"], "sections", True)) - sections:
            fail("Unknown format section")
        if set(strings(fmt.get("requires", []), "requires")) - set(meta):
            fail("Unknown required section")
        overrides = fmt.get("drop", {})
        if type(overrides) is not dict:
            fail("format drop must be an object")
        for name, drop in overrides.items():
            if name != "tags" and name not in meta:
                fail("Unknown format drop section")
            rate(drop, "format drop")
    tags = obj(
        value.get("tags", {}), {"sources", "drop", "shuffle", "exclude", "dedupe"}
    )
    if "sources" in tags:
        if type(tags["sources"]) is not list or not tags["sources"]:
            fail("tags.sources must be nonempty")
        for source in tags["sources"]:
            obj(source, {"from", "weight"}, {"from"})
            name = text(source["from"], "tag source")
            if name != "graph:root" and not re.fullmatch(
                r"meta:[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", name
            ):
                fail("Unknown tag source")
            number(source.get("weight", 1), "tag source weight", 1e-12, 1e12)
    rate(tags.get("drop", 0), "tag drop")
    if "shuffle" in tags and type(tags["shuffle"]) is not bool:
        fail("shuffle must be boolean")
    for pattern in strings(tags.get("exclude", []), "exclude"):
        if len(pattern) > 256:
            fail("Regex too long")
        try:
            re.compile(pattern)
        except re.error:
            fail("Invalid exclude regex")
    strings(tags.get("dedupe", []), "dedupe")
    for field, v in obj(value.get("nl", {}), {"short"}).items():
        number(v, "nl." + field, 0, 1)
    for field, v in obj(value.get("graph", {}), {"keep", "tag_drop"}).items():
        rate(v, "graph." + field)
    fit = obj(
        value.get("fit", {}),
        {
            "tag_drop_step",
            "tag_drop_max",
            "keep_step",
            "keep_min",
            "margin",
            "fallback",
        },
    )
    for key, v in fit.items():
        if key == "fallback":
            if set(strings(v, "fallback")) - sections:
                fail("Unknown fallback section")
        else:
            number(
                v,
                "fit." + key,
                0.001 if key.endswith("step") or key == "margin" else 0,
                1,
            )
    for key in ("prefix", "suffix", "list_sep", "section_sep", "text_sep"):
        if key in value:
            text(value[key], key, False)
    if "underscores" in value and type(value["underscores"]) is not bool:
        fail("underscores must be boolean")
    canonical(value)
    return value


def no_dynamic_imports(value):
    """Forbid executable template hooks even when inherited from a trusted base."""
    if type(value) is dict:
        if "meta_formatter" in value:
            fail("meta_formatter is not exposed by draft v1")
        for key, item in value.items():
            if (
                key in {"template", "source"}
                and type(item) is str
                and ("." in item or ":" in item)
            ):
                fail("Dotted source/template imports are forbidden")
            no_dynamic_imports(item)
    elif type(value) is list:
        for item in value:
            no_dynamic_imports(item)


def geometry(values):
    if (
        values.get("CROP") != "center"
        or values.get("RESOLUTION_MODE") == "shifted_square"
        or values.get("FLIP") is not False
    ):
        fail(
            "Geometric GBC caption requires CROP=center, FLIP=False and non-shifted_square RESOLUTION_MODE",
            "VALIDATION_FAILED",
        )
