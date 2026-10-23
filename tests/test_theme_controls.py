"""Palette, component ownership and motion gates for the three theme families."""

import re
import unittest
from pathlib import Path

from test_theme_teletype import declarations, rules, strip_comments


WEB = Path(__file__).resolve().parents[1] / "src" / "kohakumoss_ui" / "web"


class ThemeControlTests(unittest.TestCase):
    def test_theme_shapes_use_one_token_per_component_role(self):
        for theme, radii in (("kohaku", ("6px", "8px", "8px", "6px")),
                             ("soul", ("4px", "6px", "8px", "3px"))):
            css = (WEB / "themes" / (theme + ".css")).read_text()
            body = next(body for selector, body in rules(css)
                        if selector == 'html[data-theme="' + theme + '"]')
            for prop, value in zip(("--radius-sm", "--radius-md", "--radius-lg", "--radius-status"), radii):
                self.assertEqual(declarations(body, prop), [value])
        base = (WEB / "base.css").read_text()
        for prop in ("--radius-sm", "--radius-md", "--radius-lg", "--radius-status"):
            self.assertEqual(declarations(base, prop), ["0"])

    def test_standalone_tables_have_a_panel_frame(self):
        for name in ("views.js", "studio.js"):
            views = (WEB / name).read_text()
            self.assertNotIn('<table class="led surface"', views)
            self.assertNotIn('<div class="table-scroll surface"', views)
            # The frame must stay outside the scrollport, keeping its corner
            # marks and side insets stationary while the columns scroll.
            frames = views.count('<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table')
            self.assertGreater(frames, 0)
            self.assertEqual(frames, views.count('class="table-scroll"'))

    def test_headers_keep_symmetric_insets_and_one_frame(self):
        css = (WEB / "app.css").read_text()
        header = next(body for selector, body in rules(css) if selector == ".vhead")
        # Two-value padding gives both vertical edges the main/sticky inset.
        self.assertEqual(declarations(header, "padding"),
                         ["var(--view-top) var(--surface-pad-x)"])
        self.assertEqual(declarations(header, "border"), ["1px solid var(--border-default)"])
        for theme in ("kohaku", "soul", "teletype"):
            for selector, body in rules((WEB / "themes" / (theme + ".css")).read_text()):
                if ".vhead" in selector and "::" not in selector:
                    for prop in ("border", "border-bottom", "border-bottom-width", "padding-top", "padding-bottom"):
                        self.assertFalse(declarations(body, prop), (selector, prop))

    def test_teletype_marks_independent_sheets_without_breaking_sticky_header(self):
        css = (WEB / "themes/teletype.css").read_text()
        corners = [(selector, body) for selector, body in rules(css)
                   if "::before" in selector and declarations(body, "content") == ['""']]
        corner_selector, corner_body = next((selector, body) for selector, body in corners
                                            if ".vhead" in selector)
        for role in (".box", ".vhead", ".knobswork.surface", ".readystrip.surface",
                     ".newrun-head.surface", ".runs-bar.surface", ".nb-filter.surface",
                     ".table-frame.surface", ".alert.surface", ".empty.surface", "details.adv.surface"):
            self.assertIn(role, corner_selector)
        self.assertIn(":not(:is(.box,.surface) *)", corner_selector)
        self.assertNotIn(".box:not(.pad)", css)
        self.assertEqual(declarations(corner_body, "pointer-events"), ["none"])
        for selector, body in rules(css):
            if ".vhead" in selector and declarations(body, "position") == ["relative"]:
                self.assertIn(":not(.vhead-sticky)", selector)

    def test_kohaku_keeps_exactly_three_shades_per_gem(self):
        css = strip_comments((WEB / "themes/kohaku.css").read_text())
        triples = {
            "iolite": ("#DDD0F0", "#5A4FCF", "#312A7A"),
            "sapphire": ("#D6E3F8", "#0F52BA", "#082567"),
            "aquamarine": ("#D4EDE8", "#4C9989", "#1B6B5A"),
            "amber": ("#F5E6C8", "#D4920A", "#8B5E00"),
            "coral": ("#F5D5D5", "#D46B6B", "#8B3A3A"),
        }
        for gem, values in triples.items():
            slots = dict(re.findall(r"(--gem-" + gem + r"(?:-[\w-]+)?)\s*:\s*([^;]+)", css))
            self.assertEqual(slots, dict(zip(
                ("--gem-" + gem + "-light", "--gem-" + gem, "--gem-" + gem + "-shadow"),
                values,
            )))

    def test_soul_has_distinct_dark_hover_surface(self):
        css = (WEB / "themes/soul.css").read_text()
        for mode, expected in (("carbon", "#2C3136"), ("light", "#E9EDF0")):
            selector = 'html[data-theme="soul"][data-mode="' + mode + '"]'
            body = next(body for rule, body in rules(css) if rule == selector)
            self.assertEqual(declarations(body, "--hover"), [expected])

    def test_theme_rules_do_not_leak_to_other_families(self):
        for theme in ("kohaku", "soul", "teletype"):
            css = (WEB / "themes" / (theme + ".css")).read_text()
            for selector, _ in rules(css):
                self.assertIn('data-theme="' + theme + '"', selector, selector)

    def test_no_theme_adds_continuous_motion_or_surface_blur(self):
        for theme in ("kohaku", "soul", "teletype"):
            css = strip_comments((WEB / "themes" / (theme + ".css")).read_text())
            self.assertNotIn("infinite", css)
            for _, body in rules(css):
                for prop in ("backdrop-filter", "-webkit-backdrop-filter"):
                    self.assertTrue(all(value == "none" for value in declarations(body, prop)))

    def test_soul_does_not_reintroduce_tracked_uppercase_controls(self):
        css = strip_comments((WEB / "themes/soul.css").read_text())
        self.assertTrue(all(value == "0" for value in declarations(css, "letter-spacing")))
        self.assertTrue(all(value == "none" for value in declarations(css, "text-transform")))

    def test_touch_fields_preserve_the_reading_scale(self):
        for theme in ("kohaku", "soul"):
            css = strip_comments((WEB / "themes" / (theme + ".css")).read_text())
            self.assertRegex(css, r"@media\s*\(pointer:coarse\)")
            self.assertIn("font-size:max(16px,var(--fs-body))", css)


if __name__ == "__main__":
    unittest.main()
