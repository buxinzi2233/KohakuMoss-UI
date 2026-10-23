"""Static gate for the teletype design language (docs/teletype.md section 9).

Pure stdlib: reads the shipped frontend files as text and checks the handful of
rules that a regex can enforce without a CSS parser. No browser, no trainer, no
third-party dependency.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "kohakumoss_ui" / "web"
THEME_CSS = WEB / "themes" / "teletype.css"
APP_CSS = WEB / "app.css"
APPEARANCE_JS = WEB / "appearance.js"
VIEWS_JS = WEB / "views.js"
SETTINGS_JS = WEB / "settings.js"
INDEX_HTML = WEB / "index.html"
DOC = ROOT / "docs" / "teletype.md"

MODES = ('html[data-theme="teletype"][data-mode="%s"]', ("light", "carbon"))
MATERIAL = ("--perf", "--perf-hole", "--red", "--tint", "--log-text")
SIGNATURES = ("st", "led", "logbox", "tabs", "mast", "foot", "empty", "toast", "box")


def read(path):
    return path.read_text(encoding="utf-8")


def strip_comments(css):
    """Remove /* ... */ so a comment mentioning a banned word cannot fail the gate."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def declarations(css, prop):
    """Raw values of every `prop: value` declaration; regex level, no cascade."""
    pattern = r"(?<![\w])" + re.escape(prop) + r"\s*:\s*([^;{}]+)"
    return [match.group(1).strip() for match in re.finditer(pattern, css)]


def mode_block(css, mode):
    selector = MODES[0] % mode
    match = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", css)
    return match.group(1) if match else ""


def declared_vars(block):
    return {match.group(1) for match in re.finditer(r"(--[\w-]+)\s*:", block)}


def class_tokens(*paths):
    tokens = set()
    for path in paths:
        for match in re.finditer(r'class="([^"]*)"', read(path)):
            for token in match.group(1).split():
                if re.fullmatch(r"[a-z0-9][a-z0-9-]*", token):
                    tokens.add(token)
    return tokens


RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def rules(css):
    """(selector, body) pairs at one nesting level; enough for a selector gate."""
    return [(sel.strip(), body) for sel, body in RULE.findall(strip_comments(css))]


# Layout properties an appearance rule must never own: the shell is fixed, so the
# glass / opacity / wallpaper switches only repaint (docs/teletype.md section 9.7).
LAYOUT = re.compile(
    r"(?<![\w-])(margin|padding|min-height|max-height|height|min-width|max-width|width)"
    r"\s*:\s*([^;{}]+)"
)

# The tab box (frame, fill, offset, notch) belongs to app.css; a teletype rule may
# only touch `.tabs button` type rhythm (letter-spacing / line-height / font-*).
TABS = re.compile(r"\.tabs(?![-\w])")
BOX_PROPERTY = re.compile(
    r"(?<![\w-])(?:border[\w-]*|margin[\w-]*|padding[\w-]*|background[\w-]*"
    r"|position|inset|content|width|height)\s*:"
)


class TeletypeThemeTests(unittest.TestCase):
    """themes/teletype.css: banned properties and material ownership."""

    def setUp(self):
        self.css = strip_comments(read(THEME_CSS))

    def test_theme_has_no_backdrop_filter(self):
        hits = [
            line
            for line in self.css.splitlines()
            if "backdrop-filter" in line
        ]
        self.assertEqual(
            hits,
            [],
            "themes/teletype.css: backdrop-filter is banned on the opaque sheet "
            "(docs/teletype.md section 6)",
        )

    def test_theme_has_no_nonzero_border_radius(self):
        allowed = re.compile(r"^(?:0|0px)(?:\s+(?:0|0px)){0,3}$")
        bad = [value for value in declarations(self.css, "border-radius") if not allowed.match(value)]
        self.assertEqual(
            bad,
            [],
            "themes/teletype.css: border-radius must be 0 (docs/teletype.md section 6)",
        )

    def test_theme_box_shadow_is_hard_offset_only(self):
        hard = re.compile(r"^[-0-9.]+px\s+[-0-9.]+px\s+0(?:px)?(?:\s+.*)?$")
        bad = []
        for value in declarations(self.css, "box-shadow"):
            for layer in value.split(","):
                layer = layer.strip()
                if layer != "none" and not hard.match(layer):
                    bad.append(layer)
        self.assertEqual(
            bad,
            [],
            "themes/teletype.css: box-shadow must be a zero-blur hard offset "
            "such as `3px 3px 0 var(--rule)` (docs/teletype.md section 6)",
        )

    def test_theme_font_family_is_monospace_stack(self):
        own = {"var(--font-ui)", "var(--font-mono)"}
        bad = []
        for prop in ("font-family", "--font-ui", "--font-mono"):
            for value in declarations(self.css, prop):
                if value in own or "monospace" in value:
                    continue
                bad.append("%s: %s" % (prop, value))
        self.assertEqual(
            bad,
            [],
            "themes/teletype.css: only the monospace stack may be declared "
            "(docs/teletype.md section 2 / section 6)",
        )

    def test_theme_font_stack_mentions_plex_mono(self):
        stacks = [
            value
            for prop in ("--font-ui", "font-family")
            for value in declarations(self.css, prop)
        ]
        self.assertTrue(
            any("Plex Mono" in value and "monospace" in value for value in stacks),
            "themes/teletype.css: --font-ui must keep the IBM Plex Mono + monospace "
            "fallback stack",
        )

    def test_theme_light_block_declares_material_tokens(self):
        missing = [token for token in MATERIAL if token not in declared_vars(mode_block(self.css, "light"))]
        self.assertEqual(
            missing,
            [],
            'themes/teletype.css: [data-mode="light"] must declare every material token '
            "(--perf/--perf-hole/--red/--tint/--log-text)",
        )

    def test_theme_carbon_block_declares_material_tokens(self):
        missing = [token for token in MATERIAL if token not in declared_vars(mode_block(self.css, "carbon"))]
        self.assertEqual(
            missing,
            [],
            'themes/teletype.css: [data-mode="carbon"] must declare every material token '
            "(--perf/--perf-hole/--red/--tint/--log-text)",
        )

    def test_theme_has_no_dead_selectors_or_retired_marks(self):
        """Retired marks must stay deleted (teletype-cleanup audit #14/#16/#19/#20).

        `.stamp`, `.perf-edge` and `.rule-double` never had a renderer. The four
        over-designed marks were rolled back: the running stamp no longer tilts,
        the masthead has no ticket serial and no tear in its bottom rule, the
        empty state has no VOID stamp, and the toast is a whole box instead of a
        torn edge. Any of them coming back fails here.
        """
        dead = [name for name in (".stamp", ".perf-edge", ".rule-double") if name in self.css]
        self.assertEqual(
            dead,
            [],
            "themes/teletype.css: renderer-less selectors were deleted in the teletype pass "
            "(docs/teletype.md section 5)",
        )

        retired = (
            ("rotate(", "the status stamp never rotates, running or not (audit #14)"),
            ("VOID", "the empty state carries no VOID stamp (audit #19)"),
            ("NO.", "the masthead carries no ticket serial (audit #16)"),
        )
        for needle, reason in retired:
            self.assertNotIn(
                needle,
                self.css,
                "themes/teletype.css: %s, so `%s` must not appear "
                "(docs/teletype.md section 5)" % (reason, needle),
            )

        pseudo = [
            selector
            for selector, _ in rules(self.css)
            if re.search(r"\.(?:mast|empty)::(?:before|after)", selector)
        ]
        self.assertEqual(
            pseudo,
            [],
            "themes/teletype.css: `.mast` and `.empty` carry no printed pseudo-element "
            "any more (audit #16/#19)",
        )

        toast = [
            "%s { %s }" % (selector, body)
            for selector, body in rules(self.css)
            if re.search(r"\.toast(?![\w-])", selector)
        ]
        self.assertNotEqual(
            toast,
            [],
            "themes/teletype.css: the `.toast` recipe must still exist",
        )
        torn = [rule for rule in toast if "clip-path" in rule or "mask" in rule]
        self.assertEqual(
            torn,
            [],
            "themes/teletype.css: the toast is a complete box; no `clip-path`/`mask` "
            "teeth (audit #20)",
        )

    def test_theme_does_not_restyle_tab_boxes(self):
        """The tab box is app.css's: one three-sided slab biting the strip rule.

        The theme may set the type rhythm on `.tabs button` (docs/teletype.md
        section 3), but it must not own the frame: no `.tabs` strip override, no
        `border`/`margin`/`background`/`position` on a tab selector, no `::after`
        notch, and no `.on` state rule at all.
        """
        offenders = []
        for selector, body in rules(self.css):
            if not TABS.search(selector):
                continue
            boxy = BOX_PROPERTY.search(body)
            if boxy or "::after" in selector or "::before" in selector or ".on" in selector:
                offenders.append("%s { %s }" % (selector, (boxy.group(0) if boxy else "notch/state")))
        self.assertEqual(
            offenders,
            [],
            "themes/teletype.css: the tab box belongs to app.css "
            "(`.tabs button.on` keeps `margin-bottom: -1px`, so the active slab bites "
            "the strip rule and the rule breaks under it). Teletype must not override "
            "`.tabs`, its box properties, or carve a `::after` notch "
            "(docs/teletype.md section 7)",
        )


class TeletypeAppearanceTests(unittest.TestCase):
    """appearance.js: material tokens stay out of the picker derivation."""

    def setUp(self):
        self.js = read(APPEARANCE_JS)

    def resolve_array(self, name):
        """Tokens of `const NAME = [...]`, following one identifier indirection."""
        match = re.search(re.escape(name) + r"\s*=\s*([^;]+);", self.js)
        if match is None:
            return []
        value = match.group(1).strip()
        if not value.startswith("["):
            return self.resolve_array(value)
        return re.findall(r'"(--[\w-]+)"', value)

    def test_appearance_material_exempt_is_exact(self):
        match = re.search(r"MATERIAL_EXEMPT\s*=\s*\{\s*teletype\s*:\s*([\w$]+|\[[^\]]*\])\s*\}", self.js)
        self.assertIsNotNone(match, "appearance.js: MATERIAL_EXEMPT.teletype not found")
        target = match.group(1).strip()
        listed = re.findall(r'"(--[\w-]+)"', target) or self.resolve_array(target)
        self.assertEqual(
            sorted(listed),
            sorted(MATERIAL),
            "appearance.js: MATERIAL_EXEMPT.teletype must be exactly "
            "--perf/--perf-hole/--red/--tint/--log-text",
        )

    def test_appearance_derived_vars_exclude_material_tokens(self):
        match = re.search(r"DERIVED_VARS\s*=\s*\[(.*?)\]", self.js, re.S)
        self.assertIsNotNone(match, "appearance.js: DERIVED_VARS not found")
        derived = set(re.findall(r'"(--[\w-]+)"', match.group(1)))
        overlap = sorted(derived.intersection(MATERIAL))
        self.assertEqual(
            overlap,
            [],
            "appearance.js: DERIVED_VARS must not contain material tokens; "
            "the picker would overwrite them (docs/teletype.md section 3)",
        )

    def test_appearance_teletype_veil_is_re_enabled(self):
        self.assertNotIn(
            'return "transparent"',
            self.js,
            "appearance.js: teletype must paint a paper veil, not drop it "
            "(docs/teletype.md section 6)",
        )
        self.assertIn(
            "Math.max(alpha, teleVeilFloor())",
            self.js,
            "appearance.js: the teletype veil must take the contrast floor",
        )
        self.assertRegex(
            self.js,
            r"4\.5\s*\+\s*2\.5",
            "appearance.js: the veil floor must ramp from 4.5:1 at 30 % surface "
            "alpha to 7:1 at 65 % (docs/teletype.md section 6)",
        )


class TeletypeSettingsTests(unittest.TestCase):
    """settings.js: glass and surface opacity are live for all three themes."""

    def setUp(self):
        self.js = read(SETTINGS_JS)
        self.lang = json.loads((WEB / "lang" / "en.json").read_text(encoding="utf-8"))

    def test_settings_has_no_paper_mode_gate(self):
        for dead in ("paperMode", "inert in paper mode", "fixed at 100"):
            self.assertNotIn(
                dead,
                self.js,
                "settings.js: teletype no longer disables glass or surface opacity "
                "(docs/teletype.md section 9.4)",
            )

    def test_settings_opacity_slider_is_always_bound(self):
        self.assertIn('bindRange("opacity"', self.js)
        self.assertNotRegex(
            self.js,
            r'id="opacity"[^>]*disabled',
            "settings.js: the surface opacity slider must stay enabled in every theme",
        )
        # The sheet note moved into the language table so it can be translated;
        # the gate follows the copy there instead of pinning the JS source text.
        self.assertIn("settings.sheet.note", self.js)
        self.assertIn(
            "teletype thins the controls to vellum",
            self.lang["settings.sheet.note"],
            "the language table dropped the teletype vellum explanation",
        )


class TeletypeShellTests(unittest.TestCase):
    """app.css vellum block and renderer landing points."""

    def test_content_surface_has_stock_fill_without_geometry(self):
        bodies = [body for selector, body in rules(read(APP_CSS)) if selector == ".surface"]
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0].strip(), "background:var(--panel)")

    def test_reading_regions_have_surface_landing_points(self):
        """Shared regions must stay readable on every wallpaper-backed route."""
        regions = {
            "vhead", "loading", "empty", "alert", "okline", "table-frame",
            "nb-filter", "runs-bar", "knobsummary", "scroll-hint", "adv", "tabs",
        }
        missing = []
        for name in ("views.js", "studio.js", "settings.js", "app.js", "index.html"):
            for match in re.finditer(r'class="([^"]*)"', read(WEB / name)):
                classes = set(match.group(1).split())
                # Sticky headers already have opaque stock over scrolling text.
                if "vhead-sticky" in classes:
                    continue
                if classes & regions and "surface" not in classes:
                    missing.append(name + ": " + match.group(0))
        self.assertEqual(missing, [], "Reading regions missing their surface: " + str(missing))

    def test_nested_content_reuses_the_outer_surface(self):
        nested = [
            body for selector, body in rules(read(APP_CSS))
            if selector == 'html[data-theme] :is(.box,.surface) .surface'
        ]
        self.assertEqual(len(nested), 1)
        self.assertEqual(declarations(nested[0], "background"), ["transparent"])
        self.assertFalse(LAYOUT.search(nested[0]))

    def test_content_surface_joins_both_glass_paths_without_blur(self):
        css_rules = rules(read(APP_CSS))
        for prefix, stock in (
            ('html[data-glass="on"]', "--panel"),
            ('html[data-theme="teletype"][data-glass="on"]', "--paper"),
        ):
            target = prefix + " .surface"
            fills = [
                value
                for selector, body in css_rules
                if target in [part.strip() for part in selector.split(",")]
                for prop in ("background", "background-color")
                for value in declarations(body, prop)
            ]
            self.assertEqual(
                fills,
                ["color-mix(in srgb,var(%s) calc(100%% * var(--surface-alpha,0.82)),transparent)" % stock],
                target + ": content must thin exactly like boxes",
            )
        for selector, body in css_rules:
            if not re.search(r"\.surface(?![\w-])", selector):
                continue
            for prop in ("backdrop-filter", "-webkit-backdrop-filter"):
                self.assertTrue(all(value == "none" for value in declarations(body, prop)))

    def test_app_css_teletype_scope_neutralises_backdrop_filter(self):
        css = read(APP_CSS)
        pattern = re.compile(
            r"/\* Teletype vellum.*?"
            r'html\[data-theme="teletype"\][^{}]*\{[^{}]*(?:-webkit-)?backdrop-filter\s*:\s*none',
            re.S,
        )
        self.assertRegex(
            css,
            pattern,
            "app.css: the Teletype vellum block must set `backdrop-filter: none` "
            "(plus -webkit-) under a teletype selector (docs/teletype.md section 9.4)",
        )

    def test_app_css_teletype_scope_never_blurs(self):
        bad = []
        for selector, body in rules(read(APP_CSS)):
            if 'data-theme="teletype"' not in selector:
                continue
            for prop in ("backdrop-filter", "-webkit-backdrop-filter"):
                for value in declarations(body, prop):
                    if value.strip().lower() != "none":
                        bad.append("%s -> %s: %s" % (selector, prop, value))
        self.assertEqual(
            bad,
            [],
            "app.css: every backdrop-filter under a teletype selector must be `none`; "
            "teletype thins the sheet, it never frosts it (docs/teletype.md section 6)",
        )

    def test_app_css_teletype_glass_thins_controls_not_page(self):
        controls = (".box", ".surface", ".runner", ".aside", ".mast", ".foot")
        hits = [
            selector
            for selector, body in rules(read(APP_CSS))
            if 'data-theme="teletype"' in selector
            and 'data-glass="on"' in selector
            and any(name in selector for name in controls)
            and "--surface-alpha" in body
        ]
        self.assertNotEqual(
            hits,
            [],
            "app.css: the teletype glass path must thin the controls with --surface-alpha "
            "(docs/teletype.md section 2.1 / section 6)",
        )
        clobbered = [
            selector
            for selector, body in rules(read(APP_CSS))
            if 'data-theme="teletype"' in selector
            and "--surface-alpha" in body
            and re.search(r"\.app(?![\w-])", selector)
        ]
        self.assertEqual(
            clobbered,
            [],
            "app.css: .app must not read --surface-alpha; the page fill belongs to the "
            "wallpaper layer (docs/teletype.md section 2.1)",
        )

    def test_generic_glass_block_never_fills_the_page(self):
        bad = [
            selector
            for selector, body in rules(read(APP_CSS))
            if 'data-glass="on"' in selector
            and "data-theme" not in selector
            and re.search(r"\.app(?![\w-])", selector)
        ]
        self.assertEqual(
            bad,
            [],
            "app.css: glass and surface opacity own the controls only; `.app` is filled by "
            "the wallpaper layer (docs/teletype.md section 2.1)",
        )

    def test_control_fills_read_surface_alpha_one_to_one(self):
        """Every glass control fill must be `calc(100% * --surface-alpha)`.

        A per-surface ratio or a `max(..., 80%)` clamp is the defect this guards: the
        slider must move each panel's computed alpha, so 30 % / 65 % / 100 % really
        differ on screen (docs/teletype.md section 2.1 / section 6).
        """
        css = read(APP_CSS)
        self.assertNotIn(
            "max(calc(",
            css,
            "app.css: no appearance rule may clamp --surface-alpha with a max() floor; "
            "the slider thins every control 1:1 (docs/teletype.md section 2.1)",
        )
        one_to_one = re.compile(r"calc\(\s*100%\s*\*\s*var\(\s*--surface-alpha")
        fills, bad = [], []
        for selector, body in rules(css):
            if 'data-glass="on"' not in selector:
                continue
            if not any(name in selector for name in (".box", ".surface", ".runner", ".aside", ".mast", ".foot")):
                continue
            for prop in ("background", "background-color"):
                for value in declarations(body, prop):
                    if "color-mix" not in value:
                        continue
                    if one_to_one.search(value):
                        fills.append(value)
                    else:
                        bad.append("%s -> %s: %s" % (selector, prop, value))
        self.assertEqual(
            bad,
            [],
            "app.css: control fills must read `calc(100% * var(--surface-alpha))`; "
            "a ratio such as 74 %/82 % or an 80 % floor hides the slider",
        )
        self.assertGreaterEqual(
            len(fills),
            2,
            "app.css: the generic and teletype glass blocks must both thin the controls "
            "1:1 through --surface-alpha (docs/teletype.md section 9.4)",
        )

    def test_app_css_has_no_desk_mat(self):
        css = read(APP_CSS)
        self.assertNotIn(
            "--desk-pad",
            css,
            "app.css: the desk mat was deleted so appearance switches cannot resize the "
            "shell (docs/teletype.md section 2.1)",
        )
        padded = [
            selector
            for selector, body in rules(css)
            if re.search(r"(?<![\w.-])body(?![\w-])", selector)
            and LAYOUT.search(body)
        ]
        self.assertEqual(
            padded,
            [],
            "app.css: no rule may pad or size `body`; that was the layout shift the desk "
            "mat caused (docs/teletype.md section 2.1)",
        )

    def test_app_css_teletype_scope_has_no_layout_overrides(self):
        bad = []
        for selector, body in rules(read(APP_CSS)):
            if 'data-theme="teletype"' not in selector:
                continue
            for match in LAYOUT.finditer(body):
                bad.append("%s -> %s: %s" % (selector, match.group(1), match.group(2).strip()))
        self.assertEqual(
            bad,
            [],
            "app.css: teletype appearance rules must not declare padding, margin or a "
            "height; toggling glass/opacity/background never moves a box "
            "(docs/teletype.md section 9.7)",
        )

    def test_app_css_teletype_punch_band_stays_solid(self):
        solid = [
            selector
            for selector, body in rules(read(APP_CSS))
            if 'data-theme="teletype"' in selector
            and ".app::before" in selector
            and declarations(body, "background-color") == ["var(--perf)"]
        ]
        self.assertNotEqual(
            solid,
            [],
            "app.css: the teletype punch band must keep a solid var(--perf) fill; "
            "the paper edge never thins (docs/teletype.md section 5.1)",
        )

    def test_renderers_emit_at_least_six_signature_classes(self):
        emitted = class_tokens(VIEWS_JS, INDEX_HTML)
        present = [name for name in SIGNATURES if name in emitted]
        self.assertGreaterEqual(
            len(present),
            6,
            "views.js / index.html: only %d of the %d signature landing classes are emitted "
            "(%s); at least 6 are required by docs/teletype.md section 5"
            % (len(present), len(SIGNATURES), ", ".join(present)),
        )

    def test_settings_uses_field_or_seg(self):
        emitted = class_tokens(SETTINGS_JS)
        found = sorted(emitted.intersection({"field", "seg"}))
        self.assertNotEqual(
            found,
            [],
            "settings.js: the settings view must still emit `field` or `seg`; "
            "the teletype recipes in themes/teletype.css style them",
        )


class TeletypeDocTests(unittest.TestCase):
    """docs/teletype.md stays the written counterpart of the theme."""

    def test_doc_is_not_empty(self):
        self.assertTrue(
            DOC.is_file() and len(read(DOC).strip()) > 0,
            "docs/teletype.md: missing or empty",
        )

    def test_doc_lists_signature_inventory(self):
        self.assertIn(
            "## 5. Signature inventory",
            read(DOC),
            "docs/teletype.md: the Signature inventory heading is required "
            "(the static gate checks landing points against it)",
        )


if __name__ == "__main__":
    unittest.main()
