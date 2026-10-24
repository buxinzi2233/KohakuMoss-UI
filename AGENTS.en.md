# AGENTS.md — KohakuMoss UI

> Chinese version: [AGENTS.md](AGENTS.md)

This repository is the standalone local workbench for the KohakuMoss trainer. User-facing documentation lives in `README.md`; this file only records the facts and boundaries you need to know when changing code.

## Boundaries (do not cross)

- Integrate with `../KohakuMossTrainer` through the **CLI + file contract**: do not import the trainer, do not modify trainer source, do not write its run artifacts.
- `src/kohakumoss_ui/contract/` is the only layer that knows the trainer's names (`kogine`, `metrics.jsonl`, knob names, run directory layout, doctor JSON). Upstream drift is fixed only here + in tests; do not leak trainer concepts into `server/` or `web/`.
- By default listen only on `127.0.0.1` with a one-time token; the service can start and stop training processes, so do not bind it to the public internet.
- The current directory is an independent Git repository; do not add/commit/push on your own.

## Layout

```
src/kohakumoss_ui/
  cli.py                 argument parsing, root discovery, queue scheduler thread, opens the browser or a pywebview window
  paths.py               state directory (KOHAKUMOSS_UI_STATE / XDG_STATE_HOME)
  shell/browser.py       open via webbrowser
  shell/native.py        pywebview window (optional extra: native)
  server/http.py         stdlib ThreadingHTTPServer: token check, JSON API, SSE, static assets, uploads
  server/civitai.py      Civitai REST client + publish-state snapshot (API key stays in the state dir)
  contract/              root knobs configs metrics logs runs launch doctor datasets state appearance history
  store/                 jsonfile notebook queue profile: the UI's own user data (atomic writes + 0600)
  web/                   native ES modules with no build chain
    index.html base.css app.css
    api.js format.js app.js views.js settings.js appearance.js
    studio.js heatmap.js profile / notebook / queue (events delegate through data-act)
    grabber.js           single-source collection / current task results, exported by studio.js; router calls __studioCleanup for polling and listeners
    i18n.js lang/{en,zh}.json
    data/knob-docs.json  en/zh explanation + trainer doc anchor for all 144 knobs
    themes/{kohaku,soul,teletype}.css
    fonts/IBMPlexMono-*.woff2
tests/                   contract / launch / appearance / teletype guards / store / history / civitai / i18n / knob docs
```

Frontend conventions:

- Colors are defined only in `web/themes/*.css`; `base.css` / `app.css` use semantic variables only (`--canvas`, `--panel`, `--border-*`, `--text-*`, `--accent*`).
- `web/appearance.js` is the single source of truth for theme / mode / per-theme color overrides / glass / wallpaper; `settings.js` only renders controls and calls it.
- Exactly three theme families are fixed: `kohaku`, `soul`, `teletype`, each with `light` + `carbon`; do not add a fourth family. Each family has 5 override colors: `canvas`, `panel`, `text`, `border`, `accent`; an empty string means inherit the theme value, and derived colors use `color-mix()`.
- The wallpaper is a single `background-image` painted on `<html>` (veil gradient + image). Blur is baked into the image through canvas; do **not** add a full-screen DOM layer or a full-screen CSS filter; `backdrop-filter` is used only on single-instance chrome such as `.aside` / `.mast` / `.foot` (the MonadForge approach: per-card sampling drops frames and white-screens on some drivers), and never in teletype.
- The teletype material tokens `--perf` / `--perf-hole` / `--red` / `--tint` / `--log-text` are theme-owned and exempted through `MATERIAL_EXEMPT`; never add them back to `DERIVED_VARS`, or the five pickers will overwrite the paper material.
- Layers are fixed: CUSTOM BACKGROUND does exactly one thing — whether there is a wallpaper (`data-bg="on"` makes `.app` transparent, so the wallpaper shows only through the gaps between controls and through the page margins; off paints the whole page with the theme canvas). **GLASS is the only switch that thins a surface**: with glass off, `.aside` / `.mast` / `.foot` / `.box` / `.runner` are all solid `background:var(--panel)`; only with glass on do they use `color-mix(in srgb, var(--panel) calc(100% * var(--surface-alpha)), transparent)`, and only `.aside` / `.mast` / `.foot` carry `backdrop-filter`. `.app` never reads `--surface-alpha`, and no switch may change geometry (no desk-mat padding or `calc()` height). The old "background on + glass off = 82%/92% translucent" claim was wrong and has been removed: glass off is solid.
- Teletype's controls use thin paper (surface opacity) instead of frosting but ban `backdrop-filter`: the punch band stays solid, control opacity tracks the slider 1:1 (no floor — legibility comes from the veil and MASK), and the wallpaper only shows between the controls. `docs/teletype.md` (§2, §6, §9) is required reading before changing its appearance.
- `main`'s top padding and `.vhead-sticky`'s `padding-top` are a **1:1 pair** (relaxed 16px / compact 10px): change `main`'s padding and you must change that with it, or the sticky table header jumps while scrolling.
- The sidebar order is pinned in the nav of `web/index.html`, so do not reshuffle it casually: workbench = 01 New Run `#/new`, 02 Queue `#/queue`, 03 Runs `#/runs`; data = 04 Image Collection `#/grabber`, 05 Datasets `#/datasets`, 06 Artifacts `#/artifacts`; studio = 07 Profile `#/profile`, 08 Notebook `#/notebook`; system = 09 Doctor `#/doctor`, 10 Settings `#/settings`. Artifacts no longer have a group of their own (`nav.group.artifacts` was deleted from both en and zh) — they sit under data.
- Call it "training", never the old alchemy wording: `nav.queue` / `nav.profile` / `nav.notebook` read as training queue / training profile / training notebook.
- Panel-style controls edit in place: clicking the field itself enters edit mode, so do not park a RENAME / UPLOAD text button next to it (`#/profile`'s display name and portrait are click-to-rename / click-to-upload now).
- Numeric columns pair `.led th.num{text-align:right}` with `.led td.num` on the same side; the one exception is the Datasets page's Image and Caption columns, which stay left-aligned by user request and carry no `num` class.
- No Node build chain, no third-party runtime dependencies (Pillow is optional, used for upload validation). Python source stays ASCII-only.
- Copy lives only in `web/lang/{en,zh}.json` (identical key sets, checked by `tests/test_i18n.py`); render through `ctx.t(key, vars)` instead of hardcoding visible text; `data-i18n*` attributes are all replaced in `applyShell()` in `app.js`.
- New workbench pages go in `web/studio.js` (run-related views stay in `web/views.js`); events delegate through `data-act` on the view root so a re-render leaves no stale listeners; register a new page in `route()` in `app.js` and add its crumb in `CRUMBS` plus the dictionaries.
- Knob explanations come from `web/data/knob-docs.json` (condensed from the trainer's `docs/zh-CN/**`; `tests/test_knob_docs.py` checks the keys against `scripts/train/moss.py` one-to-one); never hand-write a second copy.

## Commands

Shell facts for this machine (read this first, it saves a round of errors):

- The user's login shell is **fish 4.9.3** (`/usr/bin/fish`), so write commands for fish; there is **no** PowerShell on this machine (`pwsh`/`powershell` do not exist).
- fish 4.9 supports `VAR=val cmd` prefixes and `$(...)` substitution, but has **no heredoc** (`cat <<EOF` fails with "expected a string, but found a redirection"); loops and conditionals close with `; end`, not `done`/`fi`.
- So do not feed multi-line scripts through a heredoc: write a temporary `.fish` / `.py` file and run it, or use `python -c`. The `env VAR=val cmd` form in the examples below works in both bash and fish.

Install (the trainer venv has no pip):

```bash
cd KohakuMoss-UI
uv pip install -e . --python ../KohakuMossTrainer/.venv/bin/python --no-deps
```

Run straight from source (no install needed):

```bash
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m kohakumoss_ui \
  --root /tmp/kohakumoss-demo --runs /tmp/kohakumoss-demo --state /tmp/kmstate \
  --host 127.0.0.1 --port 8791 --token test --no-open
```

`/tmp/kohakumoss-demo` is local demo data. In tests, `--state` must always point into `/tmp`; do not write test wallpapers into the real `~/.local/state/kohakumoss-ui/`.

Tests (93 of them, <1 s; the theme guard lives in `tests/test_theme_teletype.py`, the dictionary guard in `tests/test_i18n.py`, the knob-doc guard in `tests/test_knob_docs.py`):

```bash
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests -p 'test_appearance.py'
```

Verify with the minimal sufficient set, scaled by risk:

- Touching `contract/`: run the matching tests; when real processes/signals are involved, also look at `test_launch.py`.
- Touching `server/http.py`: run the tests, then do one curl smoke test; **Python has no hot reload, so you must restart the process**.
- Touching frontend styles/interaction: static files just need a refresh, but `Cache-Control: no-store` does not always override the browser cache, so hard-refresh first.
- Pure copy, comments, CSS variable color tuning: just look at the page, no need to run tests.

Common smoke and screenshot commands:

```bash
curl -s -H 'X-UI-Token: test' http://127.0.0.1:8791/api/state
curl -X POST -H 'X-UI-Token: test' -H 'Content-Type: image/png' \
  --data-binary @shot.png http://127.0.0.1:8791/api/appearance/background
chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1440,900 --virtual-time-budget=9000 \
  --screenshot=/tmp/km.png 'http://127.0.0.1:8791/?token=test#/settings'
fuser -k 8791/tcp    # stop the service; do not use pkill -f, it also kills the shell that invoked it
```

On this machine `chromium --headless` hangs in GCM registration (`registration_request.cc`) and never exits, `--virtual-time-budget` never converges, and the screenshot comes out empty; use the ego browser instead (`ego_navigate` → `ego_wait` → `ego_screenshot`) rather than burning time on headless. Themes can also be switched straight through the API: `GET /api/appearance` for the full object → edit fields → `POST /api/appearance` with the same object.

`web/*.js` is ESM, so `node --check` needs a copy renamed to `.mjs` first:

```bash
cp src/kohakumoss_ui/web/appearance.js /tmp/appearance.mjs && node --check /tmp/appearance.mjs
```

## Environment variables

`KOHAKUMOSS_ROOT` (trainer path), `KOHAKUMOSS_RUNS` (output root), `KOHAKUMOSS_PYTHON` (interpreter), `KOHAKUMOSS_UI_STATE` (UI state directory). The CLI's `--runs` / `--state` are written into the corresponding environment variables.

## API and data notes

- API: `GET /api/{state,knobs,configs,config/load,config/check,runs,run,run/log,doctor,datasets,file,appearance,appearance/background,profile,profile/avatar,heatmap,history,notebook,queue,publish,events}`; `POST /api/run/{start,stop}`, `POST /api/appearance`, `POST /api/appearance/background`, `POST /api/profile`, `POST /api/profile/avatar` (raw bytes ≤5 MB), `POST /api/notebook/{add,update,remove}`, `POST /api/queue/{add,move,update,remove,control}`, `POST /api/publish`, `POST /api/civitai/{credentials,sync}`; `DELETE /api/profile/avatar`, `DELETE /api/civitai/credentials`. `/api/events` is SSE (it pushes `{"type":"queue"}` when the queue version changes).
- The queue is serial: `cli.py` runs a 5 s scheduler thread, and the next item only starts when the queue is not paused, no managed run is alive, and the target run directory does not exist yet. A manual START sets `pausedBy="manual"`; a manual STOP never resumes the queue by itself.
- The heatmap derives per-day seconds from the `time` delta between adjacent `metrics.jsonl` lines (`Δsec` clamped to 3600, 86400 per day), cached by (path,size,mtime) with a 30 s TTL.
- There is deliberately no "prepare datasets" page: `contract/datasets.py` only has `scan` / `_count` / `_cache_info`, `cache/` is built lazily by the trainer during training, and the UI has no preprocessing action it could trigger — so the page is not offered, and never faked.
- Civitai credentials live in the state dir as `civitai.json` (0600) and the API only returns `{username,hasKey,keyHint,lastSync}` — there is **no** GET credentials route. Publish marks live in the `publish` section of `profile.json`; `state` is limited to `published`/`draft`/`unknown`.
- Except for the upload routes, the POST body must be a JSON object, otherwise 400; upload routes read the raw bytes directly and check ≤25 MB themselves.
- UI state: `appearance.json` + `backgrounds/custom.<ext>` (single background image) + `runs.json` (child processes started by the UI) + `process/*.out` + `profile.json` / `notebook.json` / `queue.json` / `civitai.json` + `avatar.<ext>`. All of it goes through `store/jsonfile.py` atomic writes; never write into the run directory.
- Static assets are served from `web/` by file name, and the `/assets/...` prefix is stripped; `_static()` calls `resolve()` and then verifies the result is still inside `web/` — keep that guard when changing this.

## Current state / do not touch

- All three themes have been redone in their own design language: `teletype.css` follows `docs/teletype.md`, `kohaku.css` follows `kohaku-gem-design` (warm gray + five gem colours in three shades), `soul.css` follows `souldesign` (five-step ramp + ember). Change themes only in `web/themes/*.css` and never touch geometry (`padding`/`height`/`font-size`/`letter-spacing`) — all three share the one layout in `app.css`.
- The perforation-strip parameters `--perf` / `--perf-hole` in `themes/teletype.css` are still awaiting user confirmation; do not adjust them on the fly.
- No real training run has happened yet (no weights/datasets); the launch path is verified only up to command construction and signals.
