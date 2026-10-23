# Teletype — design specification

Scope: the `teletype` family only, both modes (`html[data-mode="light"]`, `html[data-mode="carbon"]`).
Sources of truth: `src/kohakumoss_ui/web/themes/teletype.css`, `src/kohakumoss_ui/web/base.css`, `src/kohakumoss_ui/web/app.css`, `src/kohakumoss_ui/web/appearance.js`, `src/kohakumoss_ui/web/views.js`, `src/kohakumoss_ui/web/format.js`, `src/kohakumoss_ui/web/settings.js`, `src/kohakumoss_ui/web/index.html`.

This document is prescriptive for the owner-reversal pass. It describes the target state, and states which current behaviour is a defect.

## 1. Purpose

Teletype is machine print on continuous stationery: one monospaced face, ink on stock, a punched left margin, and structure drawn with rules instead of shadows. It is the reading surface of a training workbench — tables, logs, receipts of numbers — styled as if a 1970s line printer produced it.

It is not paper craft, not skeuomorphic stationery, not a dark filter over `kohaku`/`soul`, and not frosted glass. Nothing in this language imitates a physical object beyond the paper control and the punch; the page itself is the desk. Transparency thins a control, it never frosts it and it never changes the page fill.

## 2. Principles

1. **Ink on stock, thinner on request.** Every control is paper; the page is the desk. The two appearance layers are separate, exactly as in MonadForge. The **overall layer** — custom background, image blur, mask — owns only the `<html>` canvas: with a wallpaper on, `.app` is transparent and the photo shows through the gaps between controls; with it off, `.app` is the theme canvas/paper. The **control layer** — glass, surface opacity — owns `.box`, `.runner`, `.aside`, `.mast`, `.foot` and form controls: `--surface-alpha` thins them 1:1 and never touches `.app`. → Therefore no `backdrop-filter` (any value) in teletype, no blur, no desaturation. The implementation is the vellum block in `app.css`: `html[data-theme="teletype"][data-glass="on"]` resolves every control — chrome and `.box` alike — to `color-mix(--paper calc(100% * --surface-alpha), transparent)`, with no per-surface ratio and no alpha floor; with glass off every control is opaque stock. The punch band keeps solid `--perf`. Geometry is invariant: no padding, margin or `calc()` height belongs to this layer, so toggling glass, opacity or the wallpaper never moves a box. Readability is a contract: body ink holds ≥4.5:1 at 30 % alpha and ≥7:1 from 65 % up against the worst desk pixel, which `teleVeilFloor()` in `appearance.js` enforces by raising the paper veil — never by keeping a control thick.
2. **One voice.** A single family (`--font-ui`) carries the whole hierarchy; weight, size and tracking do the work of a second face. → Therefore no display font, no italic emphasis, no icon font, no mixed families inside one view.
3. **Rules, not shadows.** Structure is drawn: 1px hairlines, 3px double rules, hard offsets. → Therefore no blurred `box-shadow`, no `border-radius`, no elevation metaphor.
4. **Tracking is owned by the theme.** Teletype holds its own uppercase tracking and zeroes it everywhere else; the neutral layer must not pre-set it. → Therefore never letter-space lowercase prose, digits, logs or code; never inherit a global tracking value.
5. **One mark per element.** A signature mark (punch, stamp) attaches to one element and repeats only because the structure repeats (a row, a line, a table). → Therefore never stack two marks on the same box, and never decorate a wrapper that already has a mark. Tabs carry no mark of their own: the active tab is an ordinary paper slab from `app.css`, and the theme adds no notch.
6. **Red is ribbon, not status.** `--red` is the printing ribbon: accent, stamp, selection, active index. → Therefore warn/error must resolve to `--warn`, never to `--red` or `--accent`.
7. **Carbon is the same sheet at night.** Identical geometry, ink inverted, paper texture dropped, low-glare fills instead of bright slabs. → Therefore no carbon-only layout, no carbon-only mark, no dark-mode-specific spacing.
8. **Print is static.** State changes read as a reprint: colour and border flip. → Therefore no transition above `--dur-fast` (90ms), no looping animation, no easing that overshoots.

## 3. Token ownership

Three classes: **theme-only** (declared by `themes/teletype.css` in both `[data-mode]` blocks and excluded from derivation), **derived** (recomputed by `applyColors()` in `appearance.js:131-171` from the five pickers), **user-override** (the picker values themselves, written inline on `html`).

| Token | Semantics | Owner | In `color-mix` derivation |
| --- | --- | --- | --- |
| `--paper` | Sheet stock, the page field | derived | yes — source for `--perf`, `--panel-alt` |
| `--perf` | Punch-band fill (15px gutter) | theme-only | **no** — exempt, must leave `DERIVED_VARS` |
| `--perf-hole` | Punched hole dot | theme-only | **no** — exempt |
| `--ink` | Printed ink, primary text | derived | yes |
| `--dim` | Secondary print | derived | yes |
| `--rule` | Hairline rule, frame, dotted leader | derived | yes |
| `--red` | Ribbon red: accent, stamp, active index | theme-only | **no** — and never aliased by status |
| `--tint` | 5% ink wash (row hover, `.shot img`) | theme-only | **no** — exempt |
| `--tint2` | 10% ink wash (hover fills, `.code`) | derived | yes |
| `--log-text` | Log ink, carbon only today | theme-only | must also be declared in light |
| `--desk`, `--desk-fg` | Desk mat under the sheet | theme-only | no consumer — see §10 |
| `--warn` | Alarm red, independent of `--red` | theme-only | no — new token, both modes |
| `--accent` / `--status-*` | Semantic aliases | derived | yes — must point at `--red` only for `run` |

Rules of the table:

- Anything marked "no" must not be written by `applyColors()`; deleting a key from `DERIVED_VARS` (`appearance.js:86-92`) is part of this pass, not an optional cleanup.
- The five pickers (`canvas`, `panel`, `text`, `border`, `accent`) stay the only user override surface. Material tokens are user-owned **values**, not user-owned slots: the picker must not recompute them.
- Every token in this table must be declared in both `[data-theme="teletype"][data-mode="…"]` blocks. `--log-text` was the last violation (carbon only); both modes declare it now, and `--warn` / `--status-warn` / `--status-error` are declared alongside.
- `--perf` and `--perf-hole` numerals are frozen pending the user's decision (§10).
- Exemption is per theme, not global: `appearance.js` keeps `MATERIAL_EXEMPT = { teletype: [...] }` and clears that inline layer on every pass, so the picker never writes `--perf`, `--perf-hole`, `--red`, `--tint`, `--log-text` for teletype. `kohaku` and `soul` keep the pre-existing derivation because they declare none of these tokens — dropping them there would change those themes.
- `--warn` has a neutral default in `base.css` (`--warn: var(--accent)`) so themes without their own alarm hue render exactly as before; teletype overrides it in both modes and both `--status-warn`/`--status-error` alias it.

## 4. Type scale

`--scale: 1.1` (`base.css:11`) multiplies every step; the computed values below assume it. Never hardcode px in a component; always go through the token.

| Role | Token | Computed | Weight | Line-height | Tracking | Case |
| --- | --- | --- | --- | --- | --- | --- |
| Page prose | `--fs-body` | 15.4px | 400 | 1.5 | 0 | sentence |
| View headline (`.vhead h2`) | `--fs-14` | 15.4px | 700 | 1.25 | .16em | UPPER |
| Component text (`.val`, `.led`, `.alert`) | `--fs-12` | 13.2px | 400 | 1.4 | 0 | as-is |
| Data (`.led td.num`, `.ledger td.v`) | `--fs-12` | 13.2px | 400 | 1.35 | 0, `tabular-nums` | as-is |
| Log (`.logbox`, `.ln`) | `--fs-11` | 12.1px | 400 | 1.65 | 0 | as-printed |
| Label (`.btn`, `.tabs button`, `.led th`, `.box h3`, `.form .lab`) | `--fs-10` | 11.0px | 400 / 700 | 1.3 | .12–.18em | UPPER |
| Micro (`.foot`, `.nav .idx`, `.vhead .meta`) | `--fs-9`–`--fs-10` | 9.9–11.0px | 400 | 1.2 | .10em | UPPER |

Binding rules:

- Tracking exists **only** on uppercase runs. A lowercase string, a number, a path, a log line or code gets `letter-spacing: 0`. The roughly forty tracking declarations inherited from the neutral layer are the defect this reverses: teletype declares its own tracking on its own selectors.
- Tracking magnitude scales with size, not with taste: `.10em` at micro, `.14em` at label, `.16em` at headline. Two sanctioned exceptions at `.20em`: `.brand` and `.nav .grp`.
- At most two type steps per `.box`. A third step means the box is doing two jobs and must split.
- Uppercase is for labels, statuses and headlines, never for prose or file paths. `text-transform: uppercase` on a user-supplied string is forbidden.
- The only display step is `.empty b` at `--fs-13` (14.3px), 700, `.16em`.
- `.vhead h2` also renders a run name (`views.js:490`), i.e. a user-supplied string: the theme owns its size, leading and `.16em` tracking but never sets `text-transform` on it. Values (`.box h3 b`) carry no tracking — §4 outranks the `.04em` formerly written in §7.

## 5. Signature inventory

Caps are per viewport. Every row below is live in `teletype.css`; the landing selector is the one the renderer emits.

| # | Mark | Selector | Landing | Size budget | Cap |
| --- | --- | --- | --- | --- | --- |
| 1 | Punch band | `html[data-theme="teletype"] .app::before` (`teletype.css:123-131`, gutter `app.css:6`) | Left edge of the shell, full height | 15px band, 3px hole, 15×22px pitch | exactly 1; never inside `main` |
| 2 | Paper texture | `html[data-theme="teletype"] .app` (`teletype.css:115-117`) | Sheet background, light only | 1px line per 5px, ink ≤2% | 1 layer, never nested |
| 3 | Dotted leader | `html[data-theme="teletype"] .box h3::after` (`teletype.css:134`) | `.box` heading to its right edge | 1px dotted, fills remainder | 1 per `h3`; ≤6 per view |
| 4 | Brand tracking | `html[data-theme="teletype"] .brand` (`teletype.css:139`) | Masthead wordmark (`index.html:16`) | `.2em` at 11px, 700 | exactly 1 |
| 5 | Status stamp | `.st`, `.st.run`, `.st.runn` (`views.js:53`) | Table status cell, run header | 1px frame + 1px outline (double), padding `0 5px`, 18px tall; no rotation in any state | 1 per row |
| 6 | Receipt rules + row numbers | `table.led`, `.led th`, `.led td`, `.led tbody td:first-child::before` (`views.js:115,375,432,461`) | Runs ledger, datasets, checkpoints | 3px double under `th`, 1px solid under `td`; counter `3ch` in `--dim`; a table inside `.box` keeps its own index | 1 rule set; numbers on every unindexed row |
| 7 | Sprocket gutter | `.logbox` (`views.js:521,528`, `format.js:102-107`) | Left and right edges of every log panel | 2px hole, pitch = line-height, 8px inset | 1 gutter per panel; no per-line marks |
| 8 | Totals row | `.foot` (`index.html:52-60`) | Status bar, right cluster | 3px double rule above, `tabular-nums` | 1 row |
| 9 | Registration corners | Independent `.box`, `.vhead` and framed `.surface` regions, `::before` / `::after` | Top-left and bottom-right, 3px inset | 6px L, 1px stroke, `--rule` | One pair per independent sheet; none on nested controls or unframed notes |

Every selector above is emitted by a renderer. `.stamp`, `.perf-edge`, `.rule-double` and `.stepper` were deleted in this pass: no renderer emitted them.

## 6. Prohibited

| Forbidden | Reason | Allowed exception |
| --- | --- | --- |
| `backdrop-filter` (any value) | The controls are paper, the page is the wallpaper: the blur is baked into the photo, and glass thins a control instead of frosting it | none — neutralised by `html[data-theme="teletype"][data-glass="on"] { backdrop-filter: none }` (also `-webkit-`) |
| `filter: blur()` / `saturate()` on a surface | Same: light through paper is not a teletype effect | none |
| Soft `box-shadow` (blur > 0) | Shadows imply floating sheets; print sits flat | hard offset only: `3px 3px 0 var(--rule)`, `.toast` alone |
| `border-radius` > 0 | `base.css:27-29` fixes radius at 0; rounding re-imports a card language | none — holes come from `radial-gradient`, arcs from nothing |
| Transparency outside the vellum path | Only `--surface-alpha` may thin a control fill; a literal `rgba()` fill would put text on the photo without the veil contract | `html[data-theme="teletype"][data-glass="on"]` control fills reading `--surface-alpha`, plus the paper veil in `appearance.js` |
| Alpha on `--perf`, rules, stamps or type | The punch, hairlines, stamps and glyphs are printed, not transparent stock — the printed layer stays solid | none; controls thin 1:1 through `--surface-alpha` and body ink holds ≥4.5:1 at 30 % alpha, ≥7:1 from 65 %, through the veil floor and MASK |
| More than one chromatic hue | The ribbon has one colour; `--status-ok` is ink, `--status-idle` is `--dim` | `--warn` is the second hue and only for alarm |
| Rounded or filled icon sets | The language has no icons, only brackets and glyphs | `.shot img` is content, not chrome |
| Transition > 120ms, any looping `animation` | A printer does not glide or pulse | `--dur-fast` (90ms) on hover/focus/border only |
| Decorative gradients | Only two are structural: the punch `radial-gradient` and the paper `repeating-linear-gradient` | those two, nothing else |
| Second `font-family` | One voice (§2) | none |

Alpha is allowed **only** on control fills, through `--surface-alpha`: every control (`.mast`, `.aside`, `.foot`, `.runner`, `.box`) resolves to paper thinned 1:1, with no ratio and no floor. `.app` never takes alpha: with the wallpaper on it is transparent, with it off it is the theme paper. Controls get exactly as thin as the slider asks, `--perf`/`--perf-hole` never take alpha, and the veil in `appearance.js` keeps the desk paper enough that body ink still clears its contrast target.

## 7. Component recipes

**Buttons** (`.btn`, `.btn.pri`, `.btn.dngr`, `.mast button`). Label at `--fs-10` UPPER `.12em`; 3px `--border-strong` double frame, sharp corners, `1px 7px` padding (the two extra border pixels come out of the old padding, so the footprint stays `4px 10px`). Default is bare ink on paper; `.pri` inverts to ink fill; hover and active are solid flips and never gain a shadow. Disabled drops to `--text-disabled`, never below `.45` opacity.

**Inputs and selects** (`.field`, `textarea.field`, `select.field`, `details.adv .row input`). The rule under the slot is a background line, not a border: 1px `--border-strong` at rest, two 1px `--accent` lines 2px apart (3px double) on focus. No ring, no glow, no fill, no radius, and the box does not move when the second line prints. Placeholders use `--text-muted`. `textarea` follows the same rule (the block keeps its height through padding, not a full frame).

**Tabs** (`.tabs`, `.tabs button`, `.tabs button.on`). No rule of its own: `app.css` owns the box, and teletype only sets the type rhythm (§3) on `.tabs button`. A single 1px `--border-strong` rule runs under the strip; inactive labels are `--text-muted`, and the active label is a whole slab of `--canvas` with a three-sided `--border-strong` frame whose `margin-bottom: -1px` lets it bite into the strip rule, so the rule breaks beneath the label and the tab reads as pasted onto the panel. No notch, no pill, no fill beyond the active slab, no sliding indicator.

**Tables** (`.led`, `.led th`, `.led td`, `.ledger`). `th` is a label: `--fs-10` UPPER `.16em`, 3px double rule beneath (`padding: 3px 7px` keeps the header height). `td` sits on a 1px solid `--rule` and a row number is printed before the first cell by a counter (`3ch`, `--fs-10`, `--dim`); a table that already carries its own index inside a `.box` (doctor devices, resolved config) is not numbered. `.num` is right-aligned with `tabular-nums`. `.ledger` is the receipt form: key column `14ch`, dotted leader, value column at natural width, and a 1px ink rule closes every fifth row. Hover is `--tint`, never a coloured row.

**Status stamps** (`.st`, `.st.run`, `.st.runn`, `.st.stop`, `.st.done`, `.st.unknown`). Boxed uppercase at `--fs-10`, `.14em`, 700, `line-height: 1.4`: a 1px frame plus 1px outline at `1px` offset (the double frame), both in `currentColor`. Only the running state takes ribbon colour; the frame is never rotated, and every terminal state is a `--text-muted` frame. `views.js` emits `runn` (four-letter slice), so the rule names `.st.runn` as well as `.st.run`. A status is never a dot, a pill, or a coloured background.

**Log panels** (`.logbox`, `.logbox.flat`, `.ln`, `.ln .t`, `.ln .lv`, `.ln .m`). Monospaced, `--fs-11`, `1.65` line-height, `white-space: pre`, no wrap. A `radial-gradient` sprocket gutter runs down both edges (`--perf-hole`, 2px hole, 8px inset, pitch = line height) and the text keeps a 15px padding so the holes never eat content. Timestamp in `--text-muted`, level fixed at `5ch` and bold, message in `--log-text` (or `--ink` in light). `.warn` recolours the level and message to `--warn`; there is no background highlight.

**Empty states** (`.empty`, `.empty b`). A dashed 1px frame, `34px 22px` padding, centred. `<b>` is the display step at `--fs-13` UPPER `.16em` in ink; body lines in `--text-muted`. No stamp above the title.

**Toasts** (`.toast`, `.toast.ok`). A slip: opaque `--paper`, 1px `--red` frame (`.ok` switches to `--border-strong`), `10px 12px 8px` padding, max 420px, square and complete. No `clip-path`, no `mask`, no torn edge. No soft shadow, no rounded corner, no stacking animation; the only shadow is the `3px 3px 0 var(--rule)` hard offset.

**Cards and independent sheets** (`.box`, `.box.pad`, `.vhead`, framed `.surface`). One 1px `--rule` frame, registration corners from §5.9 (top-left `::before`, bottom-right `::after`, including padded cards). Page headers use the same single frame, never a second double rule, and equal vertical insets (16px relaxed, 10px compact) around the title and actions. Other panels use shared `--surface-pad-y/x` padding (10px/12px relaxed, 7px/8px compact), with no fill beyond `--paper` thinned by `--surface-alpha` under vellum (§6). Framed surfaces include the parameter workspace, readiness/configuration blocks, filters, table wrappers, alerts, empty states and standalone advanced details. Nested controls and borderless notes share the outer sheet's marks. `.box h3` is a label with the dotted leader running to the right edge; its `<b>` value is ink and untracked.

**Navigation and shell** (`.nav a`, `.nav a.on`, `.mast`, `.foot`, `.runner`). Active nav is an ink slab with a `--red` left edge in light, a `--tint2` slab in carbon. The masthead is a single unbroken rule: no serial, no tear. `.runner` and `.foot` are ledger lines: label in `--text-muted`, value in ink, digits `tabular-nums`, and `.foot` closes the screen on a 3px double rule with the values right-aligned in a `6ch` field.

## 8. Adding a new view

- [ ] Give independent reading and action regions a `.surface` when they are not already a `.box`: headings, filters, tables, notices, loading and empty states, and standalone tabs. Nested `.surface` elements share the outer sheet, so opacity is applied once. Sticky headers keep their existing opaque stock over scrolling content.
- [ ] Choose exactly one signature landing point from §5 for the new element, and state its cap. If nothing in §5 fits, the element does not need a mark.
- [ ] Use only `var(--*)` semantic tokens; no literal colour, no `rgba()` except `--tint`/`--tint2`.
- [ ] Re-read §6 before writing a declaration. `backdrop-filter`, radius, soft shadow and blur are review failures, not preferences.
- [ ] Declare both modes. Verify light and carbon side by side; carbon must differ only in ink, fill and texture presence.
- [ ] Cap check: punch band 1, brand 1, stamp 1 per row, registration corners one pair per independent framed sheet (never repeated inside it).
- [ ] Every class used must already be emitted by a renderer (`views.js`, `settings.js`, `format.js`, `app.js`, `index.html`). Styling a class that no renderer emits is forbidden — wire the renderer first.
- [ ] Numbers go through `.num` / `.ledger td.v` with `tabular-nums`; never pad with spaces.
- [ ] Mixed type steps per box ≤2; if you need a third, split the box.

## 9. Enforcement

**Phase 5 static gate** (automated, runs over `themes/teletype.css` and the teletype-scoped selectors of `base.css`/`app.css`):

1. **Banned properties.** Grep for `backdrop-filter` (outside a `backdrop-filter:none` opt-out), `filter:.*blur`, `border-radius`, `box-shadow` with a non-zero blur (the `3px 3px 0 var(--rule)` hard offset passes), `animation:` and any `transition` duration above `120ms` under a `[data-theme="teletype"]` selector. Each hit fails with file and line.
2. **Material lock.** `--perf`, `--perf-hole`, `--tint`, `--red` must be absent from `DERIVED_VARS` (`appearance.js:86-92`) and must not be assigned in `applyColors()`. Any assignment fails.
3. **Status decoupling.** `--status-warn` and `--status-error` must not resolve to `--red` or `--accent` in either mode; a new `--warn` must be present in both blocks.
4. **Vellum, not frosting.** The `/* Teletype vellum */` block in `app.css` matches `html[data-theme="teletype"][data-glass="on"]` and must (a) set `backdrop-filter: none` (plus `-webkit-`) on every glass surface, (b) resolve the control fills (`.mast`, `.box`, `.aside`, `.foot`, `.runner`) through `calc(100% * var(--surface-alpha))` while `.app` keeps out of that path, (c) keep `.app::before` solid `var(--perf)`. No appearance rule may clamp `--surface-alpha` (`max(calc(...))`, `74% *`, `82% *` are the regression): the slider must move the computed alpha of every control. `--surface-alpha` may appear only inside that block's teletype selectors and in the generic `html[data-glass="on"]` block above, which also thins controls 1:1 for `kohaku`/`soul` and never fills `.app`.
5. **Landing-point existence.** Every selector named in §5 is grepped against `views.js`, `settings.js`, `format.js`, `app.js`, `index.html`. A signature rule for a selector with zero renderer hits fails; this is what caught `.stamp`, `.perf-edge`, `.rule-double` and `.stepper`.
6. **Both-mode coverage.** Every token in §3 appears in both `[data-mode]` blocks; a carbon-only declaration (`--log-text`) fails.
7. **Fixed geometry.** No teletype rule in `app.css` may declare `padding`, `margin` or a `calc()` height on `body` / `.app`, and no teletype rule may set a `.app` background through `--surface-alpha`: the appearance switches repaint the controls, they never resize the shell (`--desk-pad` and the desk mat were deleted).

**Human review, three hard lines:**

1. No new colour. If a screen needs a new hue, the composition is wrong, not the palette.
2. One mark per element. Two signatures on one box is a rejection even when each is individually within budget.
3. No class without a renderer, and no renderer change smuggled into a theme pass. The theme owns pixels; `views.js` owns structure.

## 10. Open items

1. **Punch geometry is user-owned.** `--perf` and `--perf-hole` (`teletype.css:6-7`, `:41-42`) and the 15px band / 3px hole / 22px pitch are frozen. This pass must not tune them; changing the band width also changes `app.css:6`, so it needs explicit sign-off.
2. **`.stamp` → `.st` wiring is closed.** The stale `.stamp` rule is gone; the stamp language lives on `.st` (frame + outline); the running state keeps the ribbon colour but is never rotated. `views.js` emits `runn` because it slices the status to four characters — the CSS names both spellings, the renderer stays untouched.
3. **Dead tokens.** `--desk` has a consumer: `html[data-theme="teletype"][data-bg="on"]` paints it as the base under the wallpaper, so the page never flashes white while the image loads. `--desk-fg` is still unconsumed and should be deleted or wired in the next pass. The 18px desk frame (`--desk-pad`, `body` padding, `calc()` height, shell shadow) is gone: shell geometry is fixed and the wallpaper shows between the paper controls instead of in a mat around the page. `--row-h` (`base.css:31`) is still unconsumed: `.led td` reaches ~30px through `--lh-data` plus padding, so consuming the token would only fight the compact density.
4. **Appearance controls are shared, but layered.** `CUSTOM BACKGROUND + IMAGE BLUR + MASK` own the page: `data-bg="on"` makes `.app` transparent and shows the wallpaper, `data-bg="off"` leaves `.app` on the theme canvas. `GLASS + SURFACE OPACITY` own the controls: the slider thins `.box`/`.runner`/`.aside`/`.mast`/`.foot` 1:1 and persists in the same `surfaceOpacity` field, and GLASS is a live toggle. Teletype differs only in that blur stays banned and the paper veil is raised (`teleVeilFloor()`) to hold body ink ≥4.5:1 at 30 % alpha and ≥7:1 from 65 % up; the punch band stays solid and no control keeps an alpha floor. Stored values are shared, so switching themes never rewrites them.
