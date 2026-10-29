"""Worker-only bounded raster previews from native samples or frozen inputs."""

import base64
import hashlib
import io

from gbc_data_common import MAX_IMAGE, dataset_info, flat_name, read_bytes, require, safe_path
from gbc_data_graph import image_gate

MAX_OUTPUT = 4 << 20
SIZES = {"thumb": 384, "detail": 1600}


class BoundedOutput(io.BytesIO):
    def write(self, data):
        require(self.tell() + len(data) <= MAX_OUTPUT, "limit_exceeded", "Preview exceeds 4 MiB")
        return super().write(data)


def render(data, size):
    from PIL import Image

    require(isinstance(size, str) and size in SIZES, "invalid_request", "Unknown preview size")
    dimensions, _ = image_gate(data)
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        source.thumbnail((SIZES[size], SIZES[size]), Image.Resampling.LANCZOS)
        # Rebuild pixels, not metadata: never forward EXIF, ICC, text or source bytes.
        with source.convert("RGBA") as converted:
            with Image.new("RGBA", converted.size) as clean:
                clean.paste(converted)
                with BoundedOutput() as output:
                    clean.save(output, format="PNG")
                    encoded = base64.b64encode(output.getvalue()).decode("ascii")
                return {"mimeType": "image/png", "encoding": "base64", "base64": encoded,
                        "width": clean.width, "height": clean.height,
                        "sourceWidth": dimensions[0], "sourceHeight": dimensions[1],
                        "size": size, "orientation": "stored-image"}


def dataset_media(payload):
    from gbc_data_storage import parquet, rows, sample, shard_files, shard_reader

    key, shard = payload.get("key"), payload.get("shard")
    require(flat_name(key) and flat_name(shard), "invalid_request", "Invalid sample reference")
    size = payload.get("size", "thumb")
    require(isinstance(size, str) and size in SIZES, "invalid_request", "Unknown preview size")
    root, _, selected = dataset_info(payload.get("path"), [shard])
    paths = shard_files(root, shard)
    table = parquet(paths["manifest.parquet"])
    try:
        count = table.metadata.num_rows
        require(count == selected[0]["samples"], "invalid_dataset", "Manifest count mismatch")
        with shard_reader(paths, count) as reader:
            for index, row in enumerate(rows(table)):
                if row["key"] == key:
                    # Retain the existing graph/image/meta/index validation gate.
                    sample(reader, row, index, shard)
                    archive, members = reader[:2]
                    return render(archive.read(members[index]), size)
    finally:
        table.close()
    require(False, "not_found", "Unknown sample in selected shard")


def frozen_media(payload):
    path = safe_path(payload.get("path"))
    before = path.stat()
    data = read_bytes(path, MAX_IMAGE)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "source_changed", "Frozen image changed while reading")
    require("sha256:" + hashlib.sha256(data).hexdigest() == payload.get("hash"),
            "source_changed", "Frozen image differs from task manifest")
    return render(data, payload.get("size", "thumb"))
