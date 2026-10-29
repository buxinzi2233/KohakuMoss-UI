"""Worker-only native storage adapter with bounded, single-shard reads."""

import json
import os
import sqlite3
import stat
import struct
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pyarrow.parquet as pq
from gbc_data_common import (
    MAX_IMAGE,
    MAX_JSON,
    MAX_LINE,
    SHARD_FILES,
    Fault,
    dataset_info,
    flat_name,
    integer,
    json_value,
    read_bytes,
    require,
    safe_path,
)
from gbc_data_graph import decode_graph, graph_gate, image_gate
from gbc_data_runtime import check_parent
from kohakugbc_data.dataset import DatasetWriter
from kohakugbc_data.dataset.images import CODECS, COLORS, EXTENSIONS, _central_directory
from kohakuvault import DataPacker, KVault

COLUMNS = ("key", "index", "w", "h", "codec", "image_bytes", "graph_bytes")


def bounds(payload):
    offset, limit = payload.get("offset", 0), payload.get("limit", 20)
    require(integer(offset) and integer(limit, 1, 128), "invalid_request", "Invalid pagination bounds")
    return offset, limit


def shard_files(root, name):
    folder = safe_path(str(root / "shards" / name), "dir")
    require(set(os.listdir(folder)) == set(SHARD_FILES), "invalid_dataset", "Shard must contain exactly four completed files")
    paths = {name: safe_path(str(folder / name)) for name in SHARD_FILES}
    for filename, maximum in (("graphs.db", 256 << 20), ("meta.db", 256 << 20), ("manifest.parquet", 64 << 20)):
        require(paths[filename].stat().st_size <= maximum, "limit_exceeded", f"{filename} exceeds bounded-reader limit")
    return paths


def parquet(path):
    with open(path, "rb") as f:
        require(path.stat().st_size >= 12, "invalid_dataset", "Truncated Parquet file")
        f.seek(-8, 2)
        footer, magic = struct.unpack("<I4s", f.read(8))
        require(magic == b"PAR1" and footer <= 8 << 20, "invalid_dataset", "Invalid or oversized Parquet footer")
    table = pq.ParquetFile(path)
    try:
        require(set(COLUMNS) <= set(table.schema_arrow.names), "invalid_dataset", "Missing manifest columns")
        require(table.metadata.num_row_groups <= 10000, "limit_exceeded", "Too many Parquet row groups")
        for i in range(table.metadata.num_row_groups):
            require(table.metadata.row_group(i).total_byte_size <= 256 << 20,
                    "limit_exceeded", "Parquet row group exceeds decoded-size limit")
        return table
    except BaseException:
        table.close()
        raise


def rows(table, offset=0, limit=None):
    """Skip whole row groups and yield at most 128 materialized rows per batch."""
    produced = 0
    for group in range(table.metadata.num_row_groups):
        count = table.metadata.row_group(group).num_rows
        if offset >= count:
            offset -= count
            continue
        for batch in table.iter_batches(batch_size=128, row_groups=[group], columns=list(COLUMNS)):
            if offset >= batch.num_rows:
                offset -= batch.num_rows
                continue
            for row in batch.slice(offset).to_pylist():
                offset = 0
                if limit is not None and produced >= limit:
                    return
                yield row
                produced += 1
                if limit is not None and produced >= limit:
                    return


@contextmanager
def shard_reader(paths, expected):
    """Bound ZIP metadata before zipfile loads its central directory; never extract."""
    with ExitStack() as stack:
        with open(paths["images.zip"], "rb") as f:
            count, central_size, central_offset = _central_directory(f, paths["images.zip"].stat().st_size)
        require(count <= 100001 and central_size <= 16 << 20 and central_offset >= 0,
                "limit_exceeded", "ZIP central directory exceeds bounds")
        archive = stack.enter_context(zipfile.ZipFile(paths["images.zip"]))
        members = archive.infolist()
        require(len(members) == count == expected + 1 and members[-1].filename == "__index__.bin",
                "invalid_dataset", "Image members/index count differs from manifest")
        names = set()
        for member in members:
            mode = member.external_attr >> 16
            require(flat_name(member.filename) or member.filename == "__index__.bin",
                    "unsafe_path", "Unsafe ZIP member path")
            require(member.filename not in names and not member.is_dir()
                    and stat.S_IFMT(mode) in (0, stat.S_IFREG), "unsafe_path", "Duplicate or special ZIP member")
            names.add(member.filename)
            require(member.compress_type == zipfile.ZIP_STORED and not member.flag_bits & 1
                    and member.file_size == member.compress_size,
                    "invalid_dataset", "Images must be unencrypted stored ZIP members")
            require(member.file_size <= MAX_IMAGE, "limit_exceeded", "ZIP member exceeds 32 MiB")
        index_data = archive.read(members[-1])
        require(len(index_data) >= 16 and index_data[:8] == b"KCIMG1\0\0", "invalid_dataset", "Invalid image index magic")
        head_len = struct.unpack_from("<Q", index_data, 8)[0]
        require(head_len <= MAX_JSON and 16 + head_len <= len(index_data), "invalid_dataset", "Invalid image index header")
        header = json_value(index_data[16:16 + head_len])
        start = (16 + head_len + 7) // 8 * 8
        require(isinstance(header, dict) and type(header.get("images")) is int
                and header["images"] == expected and len(index_data) == start + expected * 24,
                "invalid_dataset", "Invalid image index length/count")
        graphs = KVault(str(paths["graphs.db"]), table="graphs", read_only=True)
        stack.callback(graphs.close)
        graphs.disable_auto_pack()
        meta = KVault(str(paths["meta.db"]), table="meta", read_only=True)
        stack.callback(meta.close)
        meta.disable_auto_pack()
        require(len(graphs) == len(meta) == expected, "invalid_dataset", "KV store counts differ from manifest")
        yield archive, members, index_data, start, graphs, meta


def sample(reader, row, expected_index, name):
    archive, members, index_data, start, graphs, meta = reader
    key = row["key"]
    require(flat_name(key), "invalid_dataset", "Invalid sample key")
    require(integer(row["index"]) and row["index"] == expected_index,
            "invalid_dataset", "Manifest indices must be contiguous and unique")
    require(integer(row["w"], 1) and integer(row["h"], 1) and row["codec"] in EXTENSIONS,
            "invalid_dataset", "Invalid manifest image attributes")
    member = members[expected_index]
    require(member.filename == f"{key}.{EXTENSIONS[row['codec']]}", "invalid_dataset", "ZIP member does not match sample key/codec")
    off, size, w, h, codec, color = struct.unpack_from("<QIIIBB", index_data, start + expected_index * 24)
    with open(archive.filename, "rb") as f:
        f.seek(member.header_offset)
        head = f.read(30)
    require(len(head) == 30 and head[:4] == b"PK\x03\x04", "invalid_dataset", "Invalid local ZIP header")
    name_len, extra_len = struct.unpack_from("<HH", head, 26)
    require(off == member.header_offset + 30 + name_len + extra_len
            and size == member.file_size == row["image_bytes"]
            and (w, h, codec, color) == (row["w"], row["h"], CODECS[row["codec"]], COLORS[row["codec"]]),
            "invalid_dataset", "Image index differs from ZIP/manifest")
    blob = graphs[key]
    require(isinstance(blob, bytes) and len(blob) == row["graph_bytes"] and len(blob) <= MAX_LINE,
            "invalid_dataset", "Graph byte length differs from manifest or limit")
    native = decode_graph(blob)
    require(native.typed, "invalid_graph", "Stored graph is not native typed data")
    _, graph, preview = graph_gate(native.to_json())
    require(graph["img_size"] == [w, h], "invalid_image", "Graph dimensions differ from manifest")
    image_gate(archive.read(member), [w, h], row["codec"])
    packed_meta = meta[key]
    require(isinstance(packed_meta, bytes) and len(packed_meta) <= MAX_LINE,
            "limit_exceeded", "Encoded metadata exceeds 2 MiB")
    # Verified kohakuvault 1.1 v1 MessagePack header. Never let native auto-pack
    # dispatch an untrusted Pickle/custom-object codec before checking its type.
    require(packed_meta.startswith(b"\x89K\x01\x04\x00\x00\x00\x00VK"),
            "invalid_dataset", "Only standard MessagePack dict metadata is allowed")
    metadata = DataPacker("msgpack").unpack(packed_meta[10:], 0)
    require(isinstance(metadata, dict), "invalid_dataset", "Sample metadata must be a dict")
    require(len(json.dumps(metadata, allow_nan=False).encode()) <= MAX_LINE,
            "limit_exceeded", "Metadata exceeds 2 MiB")
    return {"key": key, "shard": name, "meta": metadata, "graph": preview}


def inspect_dataset(payload, validate=False):
    root, info, selected = dataset_info(payload.get("path"), payload.get("shards"))
    require(info.get("images") == "kohakuclip-zip" and info.get("graphs", {}).get("codec") == "gbc-msgpack"
            and info.get("graphs", {}).get("table") == "graphs"
            and info.get("meta", {}).get("codec") == "kohakuvault-autopack"
            and info.get("meta", {}).get("table") == "meta", "invalid_dataset", "Unsupported storage codecs/tables")
    details, total = [], 0
    with tempfile.TemporaryDirectory(prefix="gbc-keys-") as temp:
        seen = sqlite3.connect(str(Path(temp) / "seen.sqlite"))
        try:
            seen.execute("CREATE TABLE seen (key TEXT PRIMARY KEY)")
            for declaration in selected:
                name = declaration["name"]
                paths = shard_files(root, name)
                table = parquet(paths["manifest.parquet"])
                try:
                    count = table.metadata.num_rows
                    require(count == declaration["samples"], "invalid_dataset", "Manifest rows differ from declaration")
                    with shard_reader(paths, count) as reader:
                        if validate:
                            for index, row in enumerate(rows(table)):
                                try:
                                    seen.execute("INSERT INTO seen VALUES (?)", (row["key"],))
                                except sqlite3.IntegrityError as exc:
                                    raise Fault("invalid_dataset", "Duplicate key across dataset") from exc
                                sample(reader, row, index, name)
                                total += 1
                            seen.commit()
                    details.append({"name": name, "declaredSamples": declaration["samples"],
                                    "manifestRows": count, "actualBytes": sum(p.stat().st_size for p in paths.values())})
                finally:
                    table.close()
        finally:
            seen.close()
    return {"format": info["format"], "version": info["version"], "declaredTotal": info["samples"],
            "declaredSamples": sum(s["declaredSamples"] for s in details),
            "manifestRows": sum(s["manifestRows"] for s in details),
            "validatedSamples": total if validate else None,
            "actualBytes": (root / "dataset.json").stat().st_size + sum(s["actualBytes"] for s in details),
            "selectedShards": [s["name"] for s in details], "shards": details,
            "complete": validate and len(selected) == len(info["shards"])}


def page(payload):
    offset, limit = bounds(payload)
    root, _, selected = dataset_info(payload.get("path"), payload.get("shards"))
    counts = []
    # Metadata only for selection; never concatenate or load a full manifest.
    for declaration in selected:
        paths = shard_files(root, declaration["name"])
        table = parquet(paths["manifest.parquet"])
        try:
            count = table.metadata.num_rows
            require(count == declaration["samples"], "invalid_dataset", "Manifest count mismatch")
            counts.append(count)
        finally:
            table.close()
    total = sum(counts)
    require(offset <= total, "invalid_request", "Offset exceeds selected dataset")
    result = {"items": [], "offset": offset, "nextOffset": None, "total": total, "shard": None}
    cursor = offset
    for declaration, count in zip(selected, counts):
        if cursor >= count:
            cursor -= count
            continue
        name = declaration["name"]
        paths = shard_files(root, name)
        table = parquet(paths["manifest.parquet"])
        try:
            with shard_reader(paths, count) as reader:
                result["items"] = [sample(reader, row, cursor + i, name)
                                   for i, row in enumerate(rows(table, cursor, limit))]
        finally:
            table.close()
        next_offset = offset + len(result["items"])
        result.update(shard=name, nextOffset=next_offset if next_offset < total else None)
        break
    return result


def graph_lines(path, offset=0):
    path = safe_path(str(path))
    with open(path, "rb") as f:
        require(offset <= os.fstat(f.fileno()).st_size, "invalid_request", "Byte offset exceeds JSONL")
        if offset:
            f.seek(offset - 1)
            require(f.read(1) == b"\n", "invalid_request", "Byte offset is not a line boundary")
        f.seek(offset)
        while True:
            start = f.tell()
            raw = f.readline(MAX_LINE + 1)
            if not raw:
                return
            require(len(raw) <= MAX_LINE, "limit_exceeded", "JSONL line exceeds 2 MiB")
            require(raw.endswith(b"\n"), "invalid_jsonl", "Partial final JSONL line")
            if not raw.strip():
                continue
            try:
                value = json_value(raw)
            except (ValueError, UnicodeError) as exc:
                raise Fault("invalid_jsonl", "Invalid JSONL record") from exc
            yield start, f.tell(), value


def graph_page(payload):
    offset, limit = bounds(payload)
    path = safe_path(payload.get("path"))
    items, next_offset = [], offset
    stream = graph_lines(path, offset)
    try:
        for start, end, value in stream:
            _, graph, preview = graph_gate(value)
            items.append({"graph": preview, "img_path": graph.get("img_path"), "offset": start})
            next_offset = end
            if len(items) >= limit:
                break
    finally:
        stream.close()
    return {"items": items, "offset": offset,
            "nextOffset": next_offset if items and next_offset < path.stat().st_size else None}


def pack(payload):
    graph_path = safe_path(payload.get("graphs"))
    image_root = safe_path(payload.get("imageRoot"), "dir")
    output_value = payload.get("output")
    require(isinstance(output_value, str) and bool(output_value), "invalid_request", "Output is required")
    output = Path(output_value)
    require(output.is_absolute() and ".." not in output.parts and output.name not in {"", ".", ".."},
            "unsafe_path", "Output must be an absolute non-traversing path")
    safe_path(str(output.parent), "dir")
    require(not os.path.lexists(output), "output_exists", "Output must not exist")
    per_shard, shard_bytes = payload.get("perShard", 128), payload.get("shardBytes", 64 << 20)
    require(integer(per_shard, 1, 128) and integer(shard_bytes, 1, 64 << 20), "invalid_request", "Invalid shard bounds")
    records = payload.get("records")
    require(isinstance(records, list) and 1 <= len(records) <= 50000, "invalid_request", "records needs 1..50000 mappings")
    mapping, keys = {}, set()
    for record in records:
        require(isinstance(record, dict) and set(record) == {"key", "image", "meta"}, "invalid_request", "Invalid frozen mapping")
        key, image, metadata = record["key"], record["image"], record["meta"]
        require(flat_name(key) and key not in keys, "invalid_request", "Keys must be unique safe flat names")
        require(isinstance(metadata, dict), "invalid_request", "Metadata must be an independent dict")
        require(len(json.dumps(metadata, allow_nan=False).encode()) <= MAX_LINE, "limit_exceeded", "Metadata exceeds 2 MiB")
        image_path = safe_path(image)
        require(image_path.is_relative_to(image_root) and str(image_path) == image and image not in mapping,
                "unsafe_path", "Image must exactly match a unique path under imageRoot")
        keys.add(key)
        mapping[image] = record
    # Reserve a new output exclusively. A dataset.json is published only after validation.
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise Fault("output_exists", "Output already exists") from exc
    # Keep failed/interrupted data for diagnosis. Never delete a source or a
    # failed export; the service owns explicit lifecycle cleanup of this output.
    partial = output / ".partial"
    partial.mkdir(mode=0o700)
    staging = partial / "dataset"
    check_parent()
    writer = DatasetWriter(staging, per_shard=per_shard, shard_bytes=shard_bytes,
                           header={"producer": "kohakumoss-ui", "orientation": "stored-image"})
    used = set()
    try:
        for _start, _end, value in graph_lines(graph_path):
            check_parent()
            native, graph, _preview = graph_gate(value)
            image = graph.get("img_path")
            require(isinstance(image, str) and image in mapping, "invalid_graph", "img_path is not an exact frozen image mapping")
            require(image not in used, "invalid_graph", "Duplicate graph for image mapping")
            record = mapping[image]
            data = read_bytes(Path(image), MAX_IMAGE)
            (w, h), codec = image_gate(data, graph["img_size"])
            size = len(data) + len(native.to_msgpack(zstd=3))
            require(size <= shard_bytes, "limit_exceeded", "Sample exceeds shard byte budget")
            # DatasetWriter's byte threshold is post-add; close early to honor our hard budget.
            if writer.images is not None and writer.size + size > shard_bytes:
                writer._close_shard()
            writer.add(record["key"], data, w, h, codec, native, record["meta"])
            used.add(image)
        require(len(used) == len(mapping), "invalid_graph", "Not every frozen record has one graph")
        writer.close()
    finally:
        if writer.images is not None:
            writer.images.file.close()
    inspection = inspect_dataset({"path": str(staging)}, validate=True)
    check_parent()
    os.rename(staging / "shards", output / "shards")
    check_parent()
    os.rename(staging / "dataset.json", output / "dataset.json")
    check_parent()
    staging.rmdir()
    partial.rmdir()
    return {"output": str(output), "inspection": inspection}
