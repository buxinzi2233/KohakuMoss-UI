"""Knob documentation table: shape, coverage and the trainer paths it cites."""

import json
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "kohakumoss_ui" / "web"
DOCS = WEB / "data" / "knob-docs.json"
TRAINER = Path(__file__).resolve().parents[2] / "KohakuMossTrainer"
SCRIPT = TRAINER / "scripts" / "train" / "moss.py"

CJK = re.compile("[\u4e00-\u9fff]")
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

CORE_KNOBS = (
    "WEIGHTS",
    "DATASETS",
    "ALGO",
    "DIM",
    "LR",
    "STEPS",
    "RESOLUTION_MODE",
    "RUN_NAME",
    "OUTPUT_DIR",
)


class KnobDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(DOCS.read_text(encoding="utf-8"))
        cls.knobs = cls.data["knobs"]
        cls.sections = cls.data["sections"]

    def test_version_and_generator_are_declared(self):
        self.assertEqual(self.data["version"], 1)
        self.assertTrue(self.data["generated_from"])

    def test_every_entry_has_three_filled_fields(self):
        for name, entry in self.knobs.items():
            with self.subTest(knob=name):
                self.assertEqual(sorted(entry), ["doc", "en", "zh"])
                for field in ("en", "zh", "doc"):
                    self.assertTrue(entry[field].strip(), f"{name}.{field} is blank")

    def test_short_enough_to_fit_the_help_panel(self):
        for name, entry in self.knobs.items():
            with self.subTest(knob=name):
                self.assertLessEqual(len(entry["en"]), 160)
                self.assertLessEqual(len(entry["zh"]), 80)

    def test_chinese_text_actually_is_chinese(self):
        for name, entry in self.knobs.items():
            with self.subTest(knob=name):
                self.assertTrue(CJK.search(entry["zh"]), f"{name}.zh has no CJK text")

    def test_core_knobs_are_covered(self):
        missing = [name for name in CORE_KNOBS if name not in self.knobs]
        self.assertFalse(missing, f"core knobs missing from the table: {missing}")

    def test_section_copy_covers_every_section(self):
        self.assertGreaterEqual(len(self.sections), 13)
        for title, entry in self.sections.items():
            with self.subTest(section=title):
                self.assertTrue(entry["en"].strip())
                self.assertTrue(entry["zh"].strip())

    @unittest.skipUnless(SCRIPT.is_file(), "trainer checkout is not available")
    def test_keys_match_the_trainer_knob_scan(self):
        from kohakumoss_ui.contract import knobs

        scanned = knobs.scan_script(SCRIPT)
        self.assertEqual(list(self.knobs), list(scanned["names"]))
        for section in scanned["sections"]:
            with self.subTest(section=section["title"]):
                self.assertIn(section["title"], self.sections)

    @unittest.skipUnless(TRAINER.is_dir(), "trainer checkout is not available")
    def test_cited_docs_exist(self):
        for name, entry in self.knobs.items():
            with self.subTest(knob=name):
                relative = entry["doc"].split("#", 1)[0]
                self.assertTrue((TRAINER / relative).exists(), f"{name}: {relative}")


if __name__ == "__main__":
    unittest.main()
