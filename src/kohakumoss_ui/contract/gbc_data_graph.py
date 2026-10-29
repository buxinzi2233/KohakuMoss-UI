"""Worker-only graph and stored-image gates. No inference or model imports."""

import io
import json
import math
import warnings
from collections import Counter, deque

from gbc_data_common import MAX_IMAGE, MAX_LINE, integer, json_value, require


def decode_graph(blob):
    """Bound a single zstd frame before handing bytes to the native decoder."""
    from kohakugbc_data import GbcGraph

    require(isinstance(blob, bytes) and 0 < len(blob) <= MAX_LINE,
            "limit_exceeded", "Encoded graph exceeds limit")
    if blob.startswith(b"\x28\xb5\x2f\xfd"):
        require(len(blob) >= 6, "invalid_graph", "Truncated zstd frame")
        descriptor = blob[4]
        require(not descriptor & 0x18, "invalid_graph", "Reserved zstd frame flags")
        single = bool(descriptor & 0x20)
        flag = descriptor >> 6
        size_length = (1 if single else 0, 2, 4, 8)[flag]
        require(size_length > 0, "limit_exceeded", "Unknown zstd decoded length is not bounded")
        position = 5
        if not single:
            window = blob[position]
            base = 1 << (10 + (window >> 3))
            require(base + (base // 8) * (window & 7) <= MAX_LINE,
                    "limit_exceeded", "Zstd window exceeds graph limit")
            position += 1
        position += (0, 1, 2, 4)[descriptor & 3]
        require(position + size_length <= len(blob), "invalid_graph", "Truncated zstd header")
        decoded = int.from_bytes(blob[position:position + size_length], "little")
        if size_length == 2:
            decoded += 256
        require(decoded <= MAX_LINE, "limit_exceeded", "Decoded graph exceeds 2 MiB")
        position += size_length
        while True:
            require(position + 3 <= len(blob), "invalid_graph", "Truncated zstd block")
            block = int.from_bytes(blob[position:position + 3], "little")
            kind, length, last = (block >> 1) & 3, block >> 3, block & 1
            require(kind != 3, "invalid_graph", "Reserved zstd block type")
            position += 3 + (1 if kind == 1 else length)
            require(position <= len(blob), "invalid_graph", "Truncated zstd payload")
            if last:
                break
        position += 4 if descriptor & 4 else 0
        require(position == len(blob), "invalid_graph", "Trailing or concatenated zstd frames")
    return GbcGraph.from_msgpack(blob)


# A compatibility policy, not a claim that normalized coordinates are clipped.
# Historical detector output has subpixel excursions (official corpus: <0.3px).
# Half a stored pixel bounds rounding slack; the fractional cap protects tiny images.
MAX_BBOX_OUTSIDE_PIXELS = 0.5
MAX_BBOX_OUTSIDE_NORMALIZED = 0.01


def bbox_preview(box, size):
    """Preserve source coordinates; intersect only a separate display rectangle."""
    require(isinstance(box, dict), "invalid_graph", "Missing bounding box")
    fields = ("left", "top", "right", "bottom")
    coords = [box.get(k) for k in fields]
    require(all(type(n) in (int, float) and math.isfinite(n) for n in coords),
            "invalid_graph", "Bounding box must be finite normalized LTRB")
    require(coords[0] < coords[2] and coords[1] < coords[3],
            "invalid_graph", "Bounding box has no positive area")
    slack = [min(MAX_BBOX_OUTSIDE_PIXELS / size[axis], MAX_BBOX_OUTSIDE_NORMALIZED)
             for axis in (0, 1, 0, 1)]
    require(all(-margin <= n <= 1 + margin for n, margin in zip(coords, slack)),
            "invalid_graph", "Bounding box exceeds half-pixel/one-percent boundary slack")
    display = [max(0, min(1, n)) for n in coords]
    require(display[0] < display[2] and display[1] < display[3],
            "invalid_graph", "Bounding box does not intersect the stored image")
    return {"outside": any(n < 0 or n > 1 for n in coords),
            "displayBbox": dict(zip(fields, display))}


def graph_gate(value):
    from kohakugbc_data import GbcGraph

    if isinstance(value, str):
        value = json_value(value)
    if isinstance(value, dict) and "graph" in value and "vertices" not in value:
        require(value.get("ok", True) is True, "invalid_graph", "Failed graph envelope")
        value = value["graph"]
        if isinstance(value, str):
            value = json_value(value)
    require(isinstance(value, dict), "invalid_graph", "Graph must be an object")
    raw = json.dumps(value, allow_nan=False)
    require(len(raw.encode()) <= MAX_LINE, "limit_exceeded", "Graph exceeds 2 MiB")
    vertices = value.get("vertices")
    require(isinstance(vertices, list) and 1 <= len(vertices) <= 4096,
            "invalid_graph", "Graph needs 1..4096 vertices")
    size = value.get("img_size")
    require(isinstance(size, list) and len(size) == 2 and all(integer(n, 1, 40000000) for n in size),
            "invalid_graph", "Graph must declare positive image dimensions")
    by_id, roots, outgoing, incoming = {}, [], [], []
    box_views = {}
    for v in vertices:
        require(isinstance(v, dict) and isinstance(v.get("vertex_id"), str),
                "invalid_graph", "Invalid vertex ID")
        vid = v["vertex_id"]
        require(vid not in by_id, "invalid_graph", "Duplicate vertex ID")
        by_id[vid] = v
        require(v.get("label") in {"image", "entity", "composition", "relation"},
                "invalid_graph", "Unknown vertex label")
        if v["label"] == "image":
            roots.append(vid)
        box_views[vid] = bbox_preview(v.get("bbox"), size)
        descs = v.get("descs")
        require(isinstance(descs, list) and bool(descs) and all(
            isinstance(d, dict) and isinstance(d.get("label"), str)
            and isinstance(d.get("text"), str) and d["text"].strip() for d in descs),
            "invalid_graph", "Descriptions must have nonempty text and label")
        tags = v.get("tags")
        if tags is not None:
            require(isinstance(tags, dict) and all(
                isinstance(k, str) and type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1
                for k, n in tags.items()), "invalid_graph", "Graph tags must be confidence mappings")
        for field, destination in (("out_edges", outgoing), ("in_edges", incoming)):
            edges = v.get(field)
            require(isinstance(edges, list), "invalid_graph", "Missing edge list")
            for e in edges:
                require(isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("source", "target", "text")),
                        "invalid_graph", "Malformed graph edge")
                require(e["source" if field == "out_edges" else "target"] == vid,
                        "invalid_graph", "Edge is attached to wrong vertex")
                destination.append((e["source"], e["target"], e["text"]))
    require(len(roots) == 1, "invalid_graph", "Graph needs exactly one image root")
    require(Counter(outgoing) == Counter(incoming) and len(set(outgoing)) == len(outgoing),
            "invalid_graph", "Edges must be unique and bidirectionally consistent")
    adjacency = {v: [] for v in by_id}
    indegree = dict.fromkeys(by_id, 0)
    for source, target, _text in outgoing:
        require(source in by_id and target in by_id, "invalid_graph", "Dangling edge")
        adjacency[source].append(target)
        indegree[target] += 1
    root = roots[0]
    require(indegree[root] == 0, "invalid_graph", "Image root has incoming edges")
    seen, pending = set(), [root]
    while pending:
        vid = pending.pop()
        if vid not in seen:
            seen.add(vid)
            pending.extend(adjacency[vid])
    require(len(seen) == len(vertices), "invalid_graph", "Graph has unreachable vertices")
    queue = deque(v for v, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        visited += 1
        for target in adjacency[queue.popleft()]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    require(visited == len(vertices), "invalid_graph", "Graph contains a cycle")
    native = GbcGraph.from_json(raw)
    require(native.typed, "invalid_graph", "Graph is not native typed data")
    try:
        prompt = native.prompt(width=size[0], height=size[1])
        require(isinstance(prompt, str) and bool(prompt.strip()), "invalid_graph", "Empty graph prompt")
    except (ValueError, TypeError, RuntimeError) as exc:
        require(False, "invalid_graph", f"Graph prompt cannot render: {exc}")
    preview = {
        "nodes": [{"id": v["vertex_id"], "label": v["label"], "bbox": v["bbox"],
                   "descriptions": v["descs"], **box_views[v["vertex_id"]]} for v in vertices],
        "edges": [{"source": s, "target": t, "text": text} for s, t, text in outgoing],
        "img_size": size, "orientation": "stored-image", "bboxSpace": "normalized-ltrb",
        "bboxDisplayPolicy": "clip-to-image",
    }
    return native, value, preview


def image_gate(data, dimensions=None, codec=None):
    from PIL import Image

    require(len(data) <= MAX_IMAGE, "limit_exceeded", "Image exceeds 32 MiB")
    # AVIF is a container, not the raw AV1 .obu codec used by this dataset.
    codecs = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp", "JPEGXL": "jxl"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_codec = codecs.get(image.format)
                require(actual_codec is not None, "invalid_image", "Unsupported stored image codec")
                require(image.width * image.height <= 40000000, "limit_exceeded", "Image exceeds 40 million pixels")
                require(image.getexif().get(274, 1) == 1, "invalid_image", "Nonidentity EXIF orientation requires explicit graph/image normalization")
                require(getattr(image, "n_frames", 1) == 1, "invalid_image", "Animated images are unsupported")
                size = [image.width, image.height]
                require(dimensions is None or list(dimensions) == size, "invalid_image", "Graph/manifest dimensions differ from stored image")
                require(codec is None or actual_codec == codec, "invalid_image", "Stored image codec mismatch")
                image.load()
                return size, actual_codec
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        require(False, "invalid_image", f"Image cannot be decoded: {exc}")
