"""Appearance record tests: per-theme colour overrides and image uploads."""

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from kohakumoss_ui.contract import appearance


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("KOHAKUMOSS_UI_STATE")
        os.environ["KOHAKUMOSS_UI_STATE"] = self._tmp.name
        self.state = Path(self._tmp.name)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KOHAKUMOSS_UI_STATE", None)
        else:
            os.environ["KOHAKUMOSS_UI_STATE"] = self._old
        self._tmp.cleanup()

    def test_defaults_have_three_themes_and_empty_overrides(self):
        data = appearance.read()
        self.assertEqual(data["theme"], "teletype")
        self.assertEqual(sorted(data["colors"]), ["kohaku", "soul", "teletype"])
        for values in data["colors"].values():
            self.assertTrue(all(value == "" for value in values.values()))

    def test_save_merges_one_theme_and_keeps_the_others(self):
        appearance.save({"colors": {"soul": {"accent": "#EBA375"}}})
        data = appearance.save({"colors": {"teletype": {"canvas": "#101413"}}})
        self.assertEqual(data["colors"]["soul"]["accent"], "#eba375")
        self.assertEqual(data["colors"]["teletype"]["canvas"], "#101413")
        self.assertEqual(data["colors"]["teletype"]["panel"], "")
        self.assertEqual(data["colors"]["kohaku"]["accent"], "")

    def test_language_round_trips_and_rejects_unknown(self):
        data = appearance.save({"lang": "zh"})
        self.assertEqual(data["lang"], "zh")
        data = appearance.save({"lang": "fr"})
        self.assertEqual(data["lang"], "zh")
        data = appearance.save({"lang": "auto"})
        self.assertEqual(data["lang"], "auto")

    def test_invalid_colour_falls_back_to_theme(self):
        data = appearance.save({"colors": {"teletype": {"accent": "red"}}})
        self.assertEqual(data["colors"]["teletype"]["accent"], "")

    def test_legacy_custom_theme_migrates_to_teletype(self):
        legacy = {
            "theme": "custom",
            "mode": "carbon",
            "custom": {
                "paper": "#202020",
                "panel": "#2a2a2a",
                "ink": "#eeeeee",
                "border": "#444444",
                "accent": "#ff8800",
            },
        }
        (self.state / "appearance.json").write_text(json.dumps(legacy), encoding="utf-8")
        data = appearance.read()
        self.assertEqual(data["theme"], "teletype")
        self.assertEqual(
            data["colors"]["teletype"],
            {"canvas": "#202020", "panel": "#2a2a2a", "text": "#eeeeee", "border": "#444444", "accent": "#ff8800"},
        )

    def test_upload_roundtrip_and_remove(self):
        data = appearance.save_background(PNG_1X1, "image/png")
        self.assertTrue(data["background"]["enabled"])
        self.assertTrue(data["backgroundUrl"].startswith("/api/appearance/background"))
        found = appearance.background_file()
        self.assertIsNotNone(found)
        self.assertEqual(found[0].read_bytes(), PNG_1X1)
        appearance.remove_background()
        self.assertIsNone(appearance.background_file())

    def test_upload_rejects_bad_type_and_wrong_magic(self):
        with self.assertRaises(ValueError):
            appearance.save_background(PNG_1X1, "text/plain")
        with self.assertRaises(ValueError):
            appearance.save_background(b"not a png", "image/png")


if __name__ == "__main__":
    unittest.main()
