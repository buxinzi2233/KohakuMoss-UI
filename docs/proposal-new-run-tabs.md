# NEW RUN — one page per tab

Proposal only: no application code, theme or contract file is touched; the previews
under `/tmp/km-newrun-proposal/` are static mocks from real `/api/knobs` data (143 knobs,
`scripts/train/moss.py`). Eight pages: six knob pages, one page for every non-core knob,
one page for the command.

## 1. Problem

`#/new` (`src/kohakumoss_ui/web/views.js`, `newView`) renders **one long form**:

- The strip is not a tab set: six `[data-scroll]` buttons only call `scrollIntoView()`
  (views.js:34). All sections share one column, so only MODEL is visible; the other five
  read as empty shells.
- 45 of the 143 knobs sit in that stacked column; the other 97 wait behind `ADVANCED ·
  143 KNOBS` (`advancedForm()`), one flat list of 13 sections unrelated to the strip.
- `START RUN` lives in `.vhead` and scrolls away; at 1440×1000 the column is ~three
  screens tall, so submitting means scrolling down and back up.
- `CHECK CONFIG` writes one line into `#checkline` at the bottom, far from the offending
  field; the strip shows nothing.
- There is no per-section URL (`#/new` is the only address), so a page state cannot be
  linked, bookmarked or reopened.

The result: a 143-knob scroll with six tabs that promise structure they do not deliver.

## 2. Proposal

Eight tabs become **eight pages**. One page = one address = one group of knobs. Every page
uses the same three bands: task banner (top, collapsible), field area (scrolling), action
bar (always visible at the bottom).

Two design decisions, both new in this revision:

- **Advanced knobs get their own page, not a fold per page.** A fold has to be opened on
  six different pages to compare values, and the summary line is the only thing a reviewer
  can scan. One page groups all 91 by the script's own sections, so the whole surface is
  reviewable in one scroll and no page needs a second state.
- **The command preview gets its own page, not a bar fold.** The bar stays one line tall
  on small windows, and the command, the config path and every `--set` override are read —
  and copied — as one block instead of a collapsed strip at the bottom edge.

### 2.1 Page map (all 143, from `GET /api/knobs`)

| Page | Sections it owns | core | advanced | total |
|---|---|---|---|---|
| `#/new/model` | MODEL, PRECISION | 8 | 7 | 15 |
| `#/new/data` | DATA | 8 | 29 | 37 |
| `#/new/adapter` | ADAPTER | 8 | 3 | 11 |
| `#/new/optimize` | OBJECTIVE, OPTIM, RUNTIME (STEPS, GRAD_ACC) | 9 | 9 | 18 |
| `#/new/memory` | MEMORY, PARALLEL, KERNELS | 9 | 22 | 31 |
| `#/new/output` | CHECKPOINT / EXPORT, SAMPLING, LOGGING, RUNTIME (OUTPUT_DIR) | 9 | 21 | 30 |
| `#/new/advanced` | every non-core knob, grouped by the sections above | — | 91 | 91 |
| `#/new/preview` | command, config path, overrides | — | — | — |

`RUN_NAME` lives in the banner, not on a page: 142 page-owned + 1 = 143. Page ownership
is UI-only; `contract/knobs.py` and the API stay the source of names. Tabs carry **no
counts** — the strip is labels only; the one count in the design is ADVANCED's total,
where the number itself is the point.

### 2.2 Core rows (real names, 8–9 per page)

- **MODEL** (15 knobs, 8 core) WEIGHTS, VARIANT, TRANSFORMER, TEXT_ENCODER, VAE,
  VAE_DTYPE, PRECISION, TRAIN_TEXT_REFINER. Advanced (7): ARCH_OVERRIDES, TEXT_DEVICE,
  TEXT_ENCODE, VAE_ENCODE, KEEP_FP32, FP16_FIX, FP16_FIX_KWARGS.
- **DATA** (37, 8 core) DATASETS, RESOLUTION_MODE, TARGET_AREA, BATCHING, MAX_TOKENS,
  CAPTION, TEXT_CACHE, NUM_WORKERS. Advanced (29): the remaining DATA knobs, from
  SIZE_STEP and ASPECT_RANGE through DATA_RESCAN and DATA_WAIT.
- **ADAPTER** (11, 8 core) ALGO, PRESET, DIM, ALPHA, DORA, DROPOUT, ADAPTER_MODE,
  LORAPLUS_RATIO. Advanced (3): ALGO_KWARGS, DORA_DETACH, TRAIN_NORMS.
- **OPTIMIZE** (18, 9 core) STEPS, GRAD_ACC, OPTIMIZER, LR, BETAS, SCHEDULE, GRAD_CLIP,
  TIMESTEP, LOSS. Advanced (9): EPS, WEIGHT_DECAY, WD_RULES, EMA, OPTIMIZER_KWARGS,
  WEIGHTING, TIMESTEP_KWARGS, LOSS_KWARGS, DRIVER.
- **MEMORY** (31, 9 core) MEMORY_PROFILE, CHECKPOINT, STREAM, OPTIM_STATE, ACT_OFFLOAD,
  GRAD_OFFLOAD, FUSED_STEP, PARALLEL, DEVICES. Advanced (22): the remaining MEMORY,
  PARALLEL and KERNELS knobs, from MEMORY_AUTO_FORCE through COMPILE.
- **OUTPUT** (30, 9 core) OUTPUT_DIR, SAVE_EVERY, SAVE_MINUTES, SAVE_FORMAT, SAVE_MERGED,
  RESUME, SAMPLE_PROMPTS, SAMPLE_EVERY, LOGGER. Advanced (21): the remaining CHECKPOINT /
  EXPORT, SAMPLING and LOGGING knobs, from KEEP_LAST through PROFILE_STEPS.

A knob is **core** when it is (a) required to start — WEIGHTS, DATASETS; (b) a recipe
selector — VARIANT, ALGO, PRESET, MEMORY_PROFILE, RESOLUTION_MODE, BATCHING, SAVE_FORMAT,
OPTIMIZER; (c) routinely edited — LR, STEPS, GRAD_ACC, DIM, ALPHA, MAX_TOKENS, SAVE_EVERY,
SAMPLE_EVERY. Everything else is on ADVANCED. Every page opens with 8–9 rows, so one page
fits on one screen at 1440×1000 with the action bar still visible; DATA owns 37 knobs but
still opens with 8.

### 2.3 ADVANCED page

- Page head: `ADVANCED`, one description line, and `91 KNOBS · NOT ON THE SIX PAGES` at the
  right edge.
- Body: one flat scroll, grouped by the script's section order — MODEL (4), DATA (29),
  ADAPTER (3), OBJECTIVE (3), OPTIM (5), PRECISION (3), MEMORY (8), PARALLEL (10),
  KERNELS (4), RUNTIME (1: DRIVER), LOGGING (7), SAMPLING (9), CHECKPOINT / EXPORT (5).
- Each group is a small caps heading with a count and a 1 px rule; the rows below are two
  label/control pairs per line. The first control starts on the page grid
  (`calc(180px * var(--scale))`), the second pair starts on the page midline with the same
  180 px label column, so both control edges and both right edges land on the grid.
- Rows are compact (no hints; the full comment stays in the label's `title`), 4 px of
  vertical padding, one-line textareas. An odd group closes with an empty pair so the row
  rule still spans the full width.
- Closing line: `RUN_NAME lives in the task banner; every other knob is on one of the
  eight pages.` — the count is exact, not approximate.

### 2.4 PREVIEW page

- `COMMAND`: one `.code` block with the exact `kogine` invocation, `user-select:all`, so a
  single click selects the whole line for copying. It wraps at spaces only; nothing is
  clipped.
- `CONFIG`: the config path, marked `read in place`.
- `OVERRIDES`: the number of flags, then one row per changed knob — `LR=0.0002` and
  `TARGET_AREA=0` — each with `--set KEY=VALUE · was <config value>` as its hint.
- Closing line, verbatim: **the config file itself is never rewritten**.
- The bar keeps the same two links (`OPEN DATA →`, `PREVIEW →`) and the same three buttons,
  so the preview page is a destination, not a mode.

### 2.5 Bands

- **Task banner** — one `.form`, two rows: CONFIG (select + hint) and RUN NAME (input +
  hint). It collapses to one line with SHOW. Both rows use the page grid.
- **Tab strip** — eight buttons, **no counts**: MODEL, DATA, ADAPTER, OPTIMIZE, MEMORY,
  OUTPUT, ADVANCED, PREVIEW. The active tab is the paper slab cut out of the strip
  (app.css `.tabs button.on`); a page with a blocking error carries a filled 5 px square.
  The right end holds `[ ] PAGE · CTRL+↵ START`.
- **Field area** — page header (title, one-line description), the page's `.form` rows, one
  closing `.okline`. No fold on any of the six pages; ADVANCED and PREVIEW are pages of
  their own.
- **Action bar** — a grid sibling below the field area, never an overlay: validation state
  on the left at the value column, `CANCEL · CHECK CONFIG · START RUN` on the right.

### 2.6 Validation, navigation, keyboard

`CHECK CONFIG` (`/api/config/check`) already returns the trainer's own message. A
client-side pre-pass maps each message to `{page, knob}` by matching real knob names, so:

- the message prints under the offending row with the alarm double-rule;
- that page's tab carries a filled square;
- the bar prints `1 BLOCKING · TARGET_AREA IN DATA` plus `OPEN DATA →`; the jump flips the
  page and focuses the field;
- the bar keeps the cost visible: `CHECK CONFIG · 6.2 s`.

`START RUN` is present on every page but disabled while any blocking item exists; the bar
explains why instead of a toast. A run is global, not per page.

- `#/new/model` … `#/new/preview`; `#/new` redirects to `#/new/model`; the config query
  survives (`#/new/data?config=…`).
- One values object lives above the pages. Switching tabs re-renders only the field area,
  so edits, the banner state and the draft never reset.
- `[` / `]` move to the previous/next page in the eight-page order, `Ctrl+Enter` submits,
  `Esc` returns to `#/runs`. Buttons remain focusable.
- Changing CONFIG reloads values for all pages.

### 2.7 STEPS / GRAD_ACC: what the trainer actually says

The trainer groups by `# ===================== SECTION =====================` comments in
`scripts/train/moss.py`; `contract/knobs.py:60-64` reads exactly those comments and nothing
else, so the API's `section` field *is* the script's grouping.

| Knob | Section in the trainer | Evidence |
|---|---|---|
| `STEPS` | **RUNTIME** | header `# RUNTIME` at moss.py:189, `STEPS` at moss.py:191 |
| `GRAD_ACC` | **RUNTIME** | moss.py:192, between the same header and `OUTPUT_DIR` (193) |
| `DRIVER` | RUNTIME | moss.py:190 |
| `OUTPUT_DIR` | RUNTIME | moss.py:193 |
| `RUN_NAME` | RUNTIME | moss.py:194 |
| `GRAD_CLIP` | OPTIM | header at moss.py:113, `GRAD_CLIP` at moss.py:122 |
| `SAVE_EVERY` | CHECKPOINT / EXPORT | header at moss.py:223, `SAVE_EVERY` at moss.py:224 |
| `SEED` | DATA | header at moss.py:42, `SEED` at moss.py:83 |

So neither belongs to OPTIM or OUTPUT: both are **RUNTIME**, next to `DRIVER`. RUNTIME is
a grab-bag (driver, run length, output path, run name), and the eight-page split has to
place its five knobs by meaning:

- `STEPS` and `GRAD_ACC` stay together, at the top of **OPTIMIZE** (run length and
  accumulation next to the optimizer and schedule, which is how they are read);
- `DRIVER` stays on **ADVANCED** under its own `RUNTIME` group heading;
- `OUTPUT_DIR` goes to **OUTPUT**; `RUN_NAME` stays in the banner.

This is the one place where the page split cuts a source section; it is deliberate and
recorded here, so nobody "fixes" it back later. `GRAD_CLIP` (OPTIM) and `SAVE_EVERY`
(CHECKPOINT / EXPORT) are not affected and sit on their own sections' pages.

### 2.8 Visual language

Unchanged teletype: 3 px double buttons, one rule per row, uppercase 0.14 em labels, no
radius, shadow or blur; only START RUN and the active tab take ink. No new colours; the
only new pattern is the section heading on ADVANCED (small caps + one 1 px rule).

## 3. Layout spec

Numbers are token-driven, so `--scale` and density still work.

- **Grid**: `.form` stays `calc(180px * var(--scale)) 1fr`. At 1440 wide with
  `--scale:1.1` and a visible field scrollbar, `.form .val` runs from **450.8** to **1413**
  (aside 217.8 + main padding 20 + label 198 → 450.8; 1420 − `--sbw` 7 = 1413). The measured
  `data-metrics` on every page reports `topRight == barRight` and `overflowFields: 0`.
- **Shell**: `main.nr` = `grid-template-rows:auto minmax(0,1fr) auto`, `padding:0`,
  `overflow:hidden`; `--gutter:20px` on all three bands; `--sbw:7px` (base.css scrollbar)
  added to the right padding of the top band and the bar; the field area owns
  `scrollbar-gutter:stable`, so a scrolling page never shifts a right edge off the grid.
- **Tab strip**: `.tabs` reused as-is (1 px rule, active label's three-sided frame biting
  `-1px`); labels only, no badge; severity mark before the label; keyboard hint at the
  right end of the same rule.
- **Field area**: page header (title, description), `.form` rows (`.lab` 180 px, `.val` =
  control + hint + message), one `.okline`. No fold, no per-page count.
- **ADVANCED grid**: `.form.nr-advgrid` = four columns,
  `calc(180px*--scale) minmax(0,1fr)` twice; a section heading spans `1 / -1`; both control
  columns end on the same right edge.
- **PREVIEW block**: `.code` inside `.val` with `flex-basis:100%`, `white-space:pre-wrap`,
  `overflow-wrap:anywhere`, `user-select:all`; the override rows are a second `.form` with
  a stronger top rule.
- **Action bar**: `border-top:3px double`; one row, state left at the value column, buttons
  right on the same right edge. The bar is outside the scroller and can never cover a field.
- **Error state**: `.field` is a theme background line, so the error rule out-specifies it
  (`html[data-theme="teletype"] .field.nr-field-bad`) and prints the focus pattern (two
  1 px rules) in `--warn`; the message is `--fs-10` in `--warn`.
- **Alignment rule**: every band's content box uses the same `--gutter` and the same right
  edge; nothing uses a negative margin to fake alignment. The action bar is a grid row,
  not `position:fixed`.

## 4. Migration

| File | Change | ~lines |
|---|---|---|
| `views.js` | `newView` keeps loader/values/command logic; render becomes banner + strip + one page; `commonForm`/`advancedForm` become `PAGE_SPECS` + `renderPage()`; the fold and the tab counts are removed | ~150 |
| `views/newrun.js` | optional: `PAGE_SPECS` (8 pages, core lists, section order for ADVANCED), `pageOf(knob)`, `renderPage()`, `renderAdvanced()`, `renderPreview()`, `checkMap` | new ~430 |
| `app.js` | route `parts[1]` for `new` (`#/new/<tab>`), pass it in `ctx`, redirect `#/new` → `#/new/model`, bind `[`/`]`/`Ctrl+Enter`; the eight-page order is one array | ~30 |
| `app.css` | new `.nr-*` block (shell, banner, page header, ADVANCED four-column grid, section heading, PREVIEW command block, action bar) — the prototype's `proto.css` is the starting block, plus `scrollbar-gutter` on the field area | ~95 |
| `contract/knobs.py` | none — page ownership is a UI concern; the API stays the only source of names, sections and order | 0 |

Risk is concentrated in three places: (1) values must survive a page switch, so the values
object and the `initial` snapshot have to move above the render, and the `--set` diff
(`initial[name] !== input.value`) must be computed from that shared object, not from the
current DOM; (2) ADVANCED is 91 rows in one DOM subtree — render it lazily on first visit if
the initial mount gets slow; (3) `CHECK CONFIG` mapping is heuristic — an unmatched message
must still be shown (attached to the page it names, else to the bar) and never swallowed.

Two-step rollout is possible and recommended:

1. **Split the pages** — routing, banner, strip, one page per tab, fixed bar, ADVANCED and
   PREVIEW pages. Validation output stays exactly as today but moves into the bar. Ships as
   a pure layout change.
2. **Localize the check** — `pageOf()`, per-field messages, tab marks, jump links, START
   RUN gating. Ships independently and can be reverted without touching the layout.

## 5. Open questions

1. ADVANCED at narrow widths: keep two pairs per line down to ~1100 px, or collapse to one
   label/control pair below that? The four-column grid is the only part that needs a
   breakpoint.
2. ADVANCED rows hide their hints (the full comment is a `title` tooltip). Is that enough,
   or should long JSON values get a visible one-line preview instead of a clipped field?
3. MEMORY still carries 31 knobs (PARALLEL 12 + KERNELS 4). It fits one screen at 8–9 core
   rows, but a ninth `DISTRIBUTED` page stays possible without touching anything else.
