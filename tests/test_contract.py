"""Contract tests: pure parsers and readers, no torch, no trainer imports."""

import json
import tempfile
import unittest
from pathlib import Path

from kohakumoss_ui.contract import knobs, logs, metrics, runs
from kohakumoss_ui.contract.root import TrainerRoot


SCRIPT = '''
"""Demo script."""

# ===================== MODEL =====================
WEIGHTS = "ComfyUI/models"  # weights root
VARIANT = "base"
# ===================== DATA =====================
DATASETS = [{"source": "kohya", "path": "data"}]
MAX_TOKENS = 16384
'''


def make_root(path: Path) -> TrainerRoot:
    (path / "scripts" / "train").mkdir(parents=True)
    (path / "scripts" / "train" / "moss.py").write_text(SCRIPT, encoding="utf-8")
    (path / "configs").mkdir()
    return TrainerRoot(path)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_knobs_sections_and_defaults(self):
        root = make_root(self.base)
        data = knobs.scan_script(root.root / "scripts" / "train" / "moss.py")
        self.assertEqual(data["count"], 4)
        self.assertEqual([section["title"] for section in data["sections"]], ["MODEL", "DATA"])
        weights = data["sections"][0]["knobs"][0]
        self.assertEqual(weights["name"], "WEIGHTS")
        self.assertEqual(weights["value"], "ComfyUI/models")
        self.assertEqual(weights["comment"], "weights root")

    def test_metrics_tail_and_downsample(self):
        path = self.base / "metrics.jsonl"
        rows = [{"step": step, "loss": 1.0 / step} for step in range(1, 101)]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        self.assertEqual(metrics.last_metric(path)["step"], 100)
        sampled = metrics.tail_metrics(path, limit=10)
        self.assertEqual(len(sampled), 10)
        self.assertEqual(sampled[0]["step"], 1)
        self.assertEqual(sampled[-1]["step"], 100)

    def test_logs_read_from_waits_for_newline(self):
        path = self.base / "log.txt"
        path.write_text("a\nb\n", encoding="utf-8")
        offset, lines = logs.read_from(path, 0)
        self.assertEqual(lines, ["a", "b"])
        with path.open("a", encoding="utf-8") as handle:
            handle.write("partial")
        new_offset, more = logs.read_from(path, offset)
        self.assertEqual(more, [])
        self.assertEqual(new_offset, offset)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(" line\n")
        _, more = logs.read_from(path, offset)
        self.assertEqual(more, ["partial line"])

    def test_run_summary_and_artifacts(self):
        root = make_root(self.base)
        run = root.runs_dir / "demo"
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps({"STEPS": 100, "ALGO": "lora"}), encoding="utf-8")
        (run / "metrics.jsonl").write_text(json.dumps({"step": 40, "loss": 0.5}) + "\n", encoding="utf-8")
        (run / "log.txt").write_text("hello\n", encoding="utf-8")
        checkpoint = run / "checkpoints" / "step_00000040"
        checkpoint.mkdir(parents=True)
        (checkpoint / "trainable.safetensors").write_bytes(b"x" * 10)
        sample = run / "samples" / "step_00000040"
        sample.mkdir(parents=True)
        (sample / "seed0.png").write_bytes(b"png")

        summary = runs.summarize(root, run)
        self.assertEqual(summary["status"], "stopped")
        self.assertEqual(summary["step"], 40)
        self.assertEqual(summary["checkpoints"], 1)
        self.assertEqual(summary["samples"], 1)

        detail = runs.detail(root, "demo")
        self.assertEqual(detail["summary"]["name"], "demo")
        self.assertEqual(detail["metrics"][-1]["loss"], 0.5)
        self.assertEqual(detail["checkpoints"][0]["name"], "step_00000040")
        self.assertEqual(detail["samples"][0]["files"][0]["name"], "seed0.png")

        self.assertTrue(runs.artifact_file(root, "demo", "samples/step_00000040/seed0.png").is_file())
        with self.assertRaises(ValueError):
            runs.artifact_file(root, "demo", "../../configs")


if __name__ == "__main__":
    unittest.main()
