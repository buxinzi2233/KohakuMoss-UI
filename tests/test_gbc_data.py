"""Real native, model-free fixtures and subprocess storage/security regressions."""

import copy
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract.gbc_data import DataError, call
from kohakumoss_ui.contract.gbc_data_common import declared_counts

REPO = Path(__file__).resolve().parents[2]
PYTHON = REPO / "KohakuMossTrainer/.venv/bin/python"
WORKER = Path(__file__).resolve().parents[1] / "src/kohakumoss_ui/contract/gbc_data_worker.py"
SRC = Path(__file__).resolve().parents[1] / "src"


def png(w=8, h=6):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\0" + bytes([32, 120, 200]) * w) * h
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def graph(image):
    edge = {"source": "", "target": "entity", "text": "blue rectangle"}
    def vertex(vid, label, ins, outs):
        return {"vertex_id": vid, "label": label,
                "bbox": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0, "confidence": None},
                "descs": [{"label": "detail", "text": "A blue rectangle."}],
                "in_edges": ins, "out_edges": outs, "sub_masks": [], "super_masks": [],
                "tags": {"blue": 0.9}}
    return {"vertices": [vertex("", "image", [], [edge]), vertex("entity", "entity", [edge], [])],
            "img_url": None, "img_path": str(image), "original_caption": None,
            "short_caption": None, "detail_caption": None, "img_size": [8, 6], "mask_inside_threshold": 0.85}


class GbcDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PYTHON.is_file():
            raise unittest.SkipTest("Trainer Python with kohakugbc-data is required")
        evidence = os.environ.get("GBC_DATA_EVIDENCE")
        cls.root = Path(tempfile.mkdtemp(prefix="native-data-", dir=evidence))
        cls.keep = bool(evidence)
        cls.images = cls.root / "images"
        cls.images.mkdir()
        cls.graphs, cls.records = [], []
        for i in range(3):
            image = cls.images / f"image-{i}.png"
            image.write_bytes(png())
            cls.graphs.append(graph(image))
            cls.records.append({"key": f"sample-{i}", "image": str(image), "meta": {"general": ["blue"], "rating": "general", "selection": {"quality_pct": 0.98}}})
        cls.jsonl = cls.root / "graphs.jsonl"
        cls.jsonl.write_text("".join(json.dumps(g) + "\n" for g in cls.graphs), encoding="utf-8")
        cls.dataset = cls.root / "packed"
        cls.packed = call(PYTHON, {"action": "pack", "graphs": str(cls.jsonl), "imageRoot": str(cls.images),
                                   "output": str(cls.dataset), "records": cls.records, "perShard": 2})
        (cls.root / "pack-result.json").write_text(json.dumps(cls.packed, indent=2), encoding="ascii")

    @classmethod
    def tearDownClass(cls):
        if not cls.keep:
            shutil.rmtree(cls.root)
        else:
            print(f"GBC_DATA_EVIDENCE={cls.root}")

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix="case-", dir=self.root))
        self.pack_serial = 0

    def error(self, payload, code=None):
        with self.assertRaises(DataError) as caught:
            call(PYTHON, payload)
        if code:
            self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    def clone(self):
        output = self.case / "dataset"
        shutil.copytree(self.dataset, output)
        return output

    def pack_graphs(self, values, **changes):
        path = self.case / "graphs.jsonl"
        path.write_text("".join(json.dumps(g) + "\n" for g in values), encoding="utf-8")
        self.pack_serial += 1
        return {"action": "pack", "graphs": str(path), "imageRoot": str(self.images),
                "output": str(self.case / f"out-{self.pack_serial}"), "records": self.records, **changes}

    def test_real_pack_inspect_validate_single_shard_page(self):
        self.assertEqual(self.packed["inspection"]["validatedSamples"], 3)
        result = call(PYTHON, {"action": "inspect", "path": str(self.dataset)})
        self.assertEqual((result["declaredSamples"], result["manifestRows"], result["validatedSamples"]), (3, 3, None))
        actual = sum(p.stat().st_size for p in self.dataset.rglob("*") if p.is_file())
        self.assertEqual(result["actualBytes"], actual)
        validated = call(PYTHON, {"action": "validate", "path": str(self.dataset)})
        self.assertTrue(validated["complete"])
        first = call(PYTHON, {"action": "page", "path": str(self.dataset), "offset": 1, "limit": 128})
        self.assertEqual([r["key"] for r in first["items"]], ["sample-1"])
        self.assertEqual(first["nextOffset"], 2)
        item = first["items"][0]
        self.assertEqual(item["meta"], self.records[1]["meta"])
        self.assertEqual(item["graph"]["nodes"][0]["id"], "")
        self.assertEqual(item["graph"]["orientation"], "stored-image")
        self.assertEqual(item["graph"]["bboxSpace"], "normalized-ltrb")
        last = call(PYTHON, {"action": "page", "path": str(self.dataset), "offset": 2})
        self.assertIsNone(last["nextOffset"])
        self.assertEqual(last["items"][0]["key"], "sample-2")
        for name, value in (("inspect", result), ("validate", validated), ("page", first)):
            (self.root / f"{name}-result.json").write_text(json.dumps(value, indent=2), encoding="ascii")

    def test_real_trainer_source_in_independent_process(self):
        code = """
import json, sys
from kohakumoss_data.sources.gbc import GbcSource
source = GbcSource(sys.argv[1])
rows = list(source.scan())
assert len(rows) == 3
record = source.record('shard_00000/sample-0')
assert record.graph.typed and record.meta['general'] == ['blue']
image = source.open('shard_00000/sample-0')
assert image.startswith(b'\\x89PNG')
torch = sys.modules.get('torch')
assert torch is None or not torch.cuda.is_initialized()
source._ds.close()
print(json.dumps({'samples': len(rows), 'typed': record.graph.typed, 'imageBytes': len(image), 'torchImported': torch is not None, 'cudaInitialized': False}))
"""
        done = subprocess.run([str(PYTHON), "-I", "-B", "-c", code, str(self.dataset)], check=False, capture_output=True, text=True, timeout=60)
        (self.root / "trainer-source.stdout").write_text(done.stdout, encoding="utf-8")
        (self.root / "trainer-source.stderr").write_text(done.stderr, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["samples"], 3)

    def test_graph_byte_pagination_and_partial_line(self):
        first = call(PYTHON, {"action": "graph_page", "path": str(self.jsonl), "limit": 1})
        next_page = call(PYTHON, {"action": "graph_page", "path": str(self.jsonl), "offset": first["nextOffset"], "limit": 1})
        self.assertEqual(next_page["items"][0]["img_path"], self.records[1]["image"])
        self.error({"action": "graph_page", "path": str(self.jsonl), "offset": 2}, "invalid_request")
        partial = self.case / "half.jsonl"
        partial.write_bytes(self.jsonl.read_bytes().rstrip(b"\n"))
        self.error({"action": "graph_page", "path": str(partial)}, "invalid_jsonl")
        self.error({"action": "pack", "graphs": str(partial), "imageRoot": str(self.images), "output": str(self.case / "out"), "records": self.records}, "invalid_jsonl")
        self.assertTrue((self.case / "out/.partial").is_dir())
        self.assertFalse((self.case / "out/dataset.json").exists())
        self.assertEqual(partial.read_bytes(), self.jsonl.read_bytes().rstrip(b"\n"))

    def test_shard_selection_rejects_empty_unknown_traversal(self):
        for shards in ([], ["unknown"], ["../bad"], ["shard_00000", "shard_00000"]):
            self.error({"action": "inspect", "path": str(self.dataset), "shards": shards}, "invalid_request")
        one = call(PYTHON, {"action": "validate", "path": str(self.dataset), "shards": ["shard_00001"]})
        self.assertEqual(one["validatedSamples"], 1)
        self.assertFalse(one["complete"])

    def test_graph_gates_duplicates_edges_bbox_cycles_roots_and_types(self):
        mutations = [
            lambda g: g["vertices"].append(copy.deepcopy(g["vertices"][1])),
            lambda g: g["vertices"][1].update(in_edges=[]),
            lambda g: g["vertices"][1]["bbox"].update(left=1.0),
            lambda g: g["vertices"][1]["bbox"].update(right=1.1),
            lambda g: g["vertices"][1].update(label="image"),
            lambda g: g.update(unknown=True),
            lambda g: g["vertices"][1].update(tags=["blue"]),
            lambda g: g["vertices"][1]["descs"][0].update(text=""),
            lambda g: g["vertices"][0]["out_edges"][0].update(target="missing"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                values = copy.deepcopy(self.graphs)
                mutate(values[0])
                self.error(self.pack_graphs(values), "invalid_graph")
        values = copy.deepcopy(self.graphs)
        edge = {"source": "entity", "target": "", "text": "back"}
        values[0]["vertices"][1]["out_edges"].append(edge)
        values[0]["vertices"][0]["in_edges"].append(edge)
        self.error(self.pack_graphs(values), "invalid_graph")
        values = copy.deepcopy(self.graphs)
        values[0]["vertices"][0]["out_edges"] = []
        values[0]["vertices"][1]["in_edges"] = []
        self.error(self.pack_graphs(values), "invalid_graph")

    def test_exact_mapping_and_duplicate_graphs_keys(self):
        values = copy.deepcopy(self.graphs)
        values[0]["img_path"] = Path(values[0]["img_path"]).name
        self.error(self.pack_graphs(values), "invalid_graph")
        self.error(self.pack_graphs(self.graphs + [self.graphs[0]]), "invalid_graph")
        records = copy.deepcopy(self.records)
        records[1]["key"] = records[0]["key"]
        self.error(self.pack_graphs(self.graphs, records=records), "invalid_request")
        records[1]["key"] = "../escape"
        self.error(self.pack_graphs(self.graphs, records=records), "invalid_request")

    def test_no_output_overwrite(self):
        self.error(self.pack_graphs(self.graphs, output=str(self.dataset)), "output_exists")
        self.assertTrue((self.dataset / "dataset.json").is_file())

    def test_image_dimension_codec_and_exif(self):
        values = copy.deepcopy(self.graphs)
        values[0]["img_size"] = [6, 8]
        self.error(self.pack_graphs(values), "invalid_image")
        image = self.case / "rotated.jpg"
        done = subprocess.run([str(PYTHON), "-I", "-B", "-c",
            "from PIL import Image; import sys; im=Image.new('RGB',(8,6)); ex=Image.Exif(); ex[274]=6; im.save(sys.argv[1],exif=ex)", str(image)], check=False, capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.error(self.pack_graphs([graph(image)], imageRoot=str(self.case), records=[{"key": "rotated", "image": str(image), "meta": {}}]), "invalid_image")

    def test_symlink_hardlink_special_files_and_parent_symlink(self):
        symlink = self.case / "alias"
        symlink.symlink_to(self.dataset, target_is_directory=True)
        self.error({"action": "inspect", "path": str(symlink)}, "unsafe_path")
        hardlink = self.case / "linked.jsonl"
        os.link(self.jsonl, hardlink)
        try:
            self.error({"action": "graph_page", "path": str(hardlink)}, "unsafe_path")
        finally:
            hardlink.unlink()
        fifo = self.case / "fifo"
        os.mkfifo(fifo)
        self.error({"action": "graph_page", "path": str(fifo)}, "unsafe_path")
        self.error(self.pack_graphs(self.graphs, output=str(symlink / "output")), "unsafe_path")

    def test_bad_shard_and_dangerous_zip_members(self):
        dataset = self.clone()
        manifest = dataset / "shards/shard_00000/manifest.parquet"
        manifest.write_bytes(b"broken")
        self.error({"action": "inspect", "path": str(dataset)}, "invalid_dataset")
        archive = self.case / "dataset/shards/shard_00001/images.zip"
        # Use a fresh clone per adversarial member; preserve the standard final index.
        for name in ("../escape.png", "/absolute.png", "folder\\escape.png"):
            output = self.case / f"unsafe-{len(list(self.case.iterdir()))}"
            shutil.copytree(self.dataset, output)
            archive = output / "shards/shard_00001/images.zip"
            with zipfile.ZipFile(archive) as z:
                contents = [(i.filename, z.read(i)) for i in z.infolist()]
            with zipfile.ZipFile(archive, "w") as z:
                for index, (original, data) in enumerate(contents):
                    z.writestr(name if index == 0 else original, data)
            self.error({"action": "inspect", "path": str(output)}, "unsafe_path")

    def test_duplicate_dataset_keys_and_corrupt_kv(self):
        dataset = self.clone()
        first = dataset / "shards/shard_00000"
        second = dataset / "shards/shard_00001"
        shutil.rmtree(second)
        shutil.copytree(first, second)
        info_path = dataset / "dataset.json"
        info = json.loads(info_path.read_text())
        info["samples"] = 4
        info["shards"][1]["samples"] = 2
        info_path.write_text(json.dumps(info), encoding="ascii")
        self.error({"action": "validate", "path": str(dataset)}, "invalid_dataset")
        (second / "graphs.db").write_bytes(b"not sqlite")
        self.error({"action": "validate", "path": str(dataset)})

    def test_pickle_metadata_never_executes(self):
        dataset = self.clone()
        marker = self.case / "pickle-executed"
        code = """
import sys, sqlite3
from kohakuvault import KVault, Pickle
class Poison:
    def __reduce__(self):
        return (eval, ("__import__('pathlib').Path(" + repr(sys.argv[2]) + ").write_text('unsafe')",))
v = KVault(sys.argv[1], table='meta')
v['sample-0'] = Pickle(Poison())
v.close()
c = sqlite3.connect(sys.argv[1])
c.execute('PRAGMA journal_mode=DELETE')
c.close()
"""
        done = subprocess.run([str(PYTHON), "-I", "-B", "-c", code,
                               str(dataset / "shards/shard_00000/meta.db"), str(marker)],
                              check=False, capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.error({"action": "validate", "path": str(dataset)}, "invalid_dataset")
        self.error({"action": "page", "path": str(dataset), "offset": 0}, "invalid_dataset")
        self.assertFalse(marker.exists())

    def test_missing_dependency_and_no_command_execution(self):
        result = subprocess.run([str(PYTHON), "-I", "-S", "-B", str(WORKER)],
                                check=False, input=json.dumps({"action": "inspect", "path": str(self.dataset)}),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "missing_dependency")
        marker = self.case / "executed"
        self.error({"action": f"inspect; touch {marker}", "path": str(self.dataset)}, "invalid_request")
        self.error({"action": "inspect", "path": str(self.dataset), "code": f"open({str(marker)!r},'w')"}, "invalid_request")
        self.error({"action": "inspect", "path": str(self.dataset) + f";touch {marker}"})
        self.assertFalse(marker.exists())

    def test_ui_imports_remain_stdlib_and_counts_are_conservative(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); from kohakumoss_ui.contract import gbc_data, datasets; assert not set(('PIL','pyarrow','numpy','kohakugbc_data','kohakumoss_data','torch')) & set(sys.modules)"
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(SRC)], check=False, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = declared_counts(self.dataset)
        self.assertEqual(counts["declaredSamples"], 3)
        self.assertIsNone(counts["images"])
        self.assertIsNone(counts["validatedSamples"])
        self.assertEqual(declared_counts(self.dataset, [])["countStatus"], "unavailable")
        from kohakumoss_ui.contract import datasets
        root = type("Root", (), {"root": self.root})()
        with patch.object(datasets.configs, "list_configs", return_value=[{"dir": "user", "path": "configs/user/data.py"}]), patch.object(datasets.configs, "load_config", return_value={"DATASETS": [{"source": "gbc", "path": str(self.dataset)}]}):
            result = datasets.scan(root, refresh=True)
        self.assertEqual(result["datasets"][0]["declaredSamples"], 3)
        self.assertIsNone(result["datasets"][0]["images"])

    def test_bounds_timeout_and_path_traversal(self):
        self.error({"action": "page", "path": str(self.dataset), "limit": 129}, "invalid_request")
        self.error({"action": "page", "path": str(self.dataset), "offset": True}, "invalid_request")
        self.error({"action": "inspect", "path": str(self.dataset) + "/../packed"}, "unsafe_path")
        self.error(self.pack_graphs(self.graphs, perShard=129), "invalid_request")
        self.error(self.pack_graphs(self.graphs, shardBytes=1), "limit_exceeded")
        with self.assertRaises(DataError) as caught:
            call(PYTHON, {"action": "inspect", "path": str(self.dataset)}, timeout=0.000001)
        self.assertEqual(caught.exception.code, "timeout")


if __name__ == "__main__":
    unittest.main()
