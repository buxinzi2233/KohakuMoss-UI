"""Language tables: both files carry the same keys and every literal key exists."""

import json
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "kohakumoss_ui" / "web"
LANG = WEB / "lang"

CJK = re.compile("[\u4e00-\u9fff]")
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
# t("literal.key") and t('literal.key'): template-literal keys are dynamic and skipped.
LITERAL_KEY = re.compile(r"""\bt\(\s*["']([a-zA-Z0-9_.-]+)["']""")
DATA_I18N = re.compile(r'data-i18n(?:-title|-aria|-ph)?="([^"]+)"')


def load(name):
    return json.loads((LANG / f"{name}.json").read_text(encoding="utf-8"))


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.en = load("en")
        cls.zh = load("zh")

    def test_same_key_set(self):
        self.assertEqual(sorted(self.en), sorted(self.zh))
        self.assertGreater(len(self.en), 400)

    def test_no_blank_values(self):
        for name, table in (("en", self.en), ("zh", self.zh)):
            for key, value in table.items():
                with self.subTest(lang=name, key=key):
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip(), f"{name}:{key} is blank")
                    self.assertEqual(value, value.strip(), f"{name}:{key} has stray spaces")

    def test_placeholders_survive_translation(self):
        for key, value in self.en.items():
            with self.subTest(key=key):
                self.assertEqual(
                    sorted(PLACEHOLDER.findall(value)),
                    sorted(PLACEHOLDER.findall(self.zh[key])),
                    f"{key}: placeholder set differs between en and zh",
                )

    def test_chinese_table_is_mostly_chinese(self):
        translated = sum(1 for value in self.zh.values() if CJK.search(value))
        ratio = translated / len(self.zh)
        self.assertGreater(ratio, 0.8, f"only {ratio:.0%} of zh values contain CJK")

    def test_static_markup_keys_exist(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for key in DATA_I18N.findall(html):
            with self.subTest(key=key):
                self.assertIn(key, self.en)

    def test_script_literal_keys_exist(self):
        missing = {}
        for path in sorted(WEB.glob("*.js")):
            text = path.read_text(encoding="utf-8")
            for key in LITERAL_KEY.findall(text):
                if key not in self.en:
                    missing.setdefault(key, []).append(path.name)
        self.assertFalse(missing, f"keys used in scripts but absent from en.json: {missing}")

    def test_language_choices_match_the_backend(self):
        from kohakumoss_ui.contract import appearance

        self.assertEqual(appearance.LANGUAGES, ("auto", "en", "zh"))
        self.assertIn(appearance.DEFAULT["lang"], appearance.LANGUAGES)


if __name__ == "__main__":
    unittest.main()
