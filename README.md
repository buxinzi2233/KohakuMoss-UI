# KohakuMoss UI

A local workbench for the [KohakuMoss trainer](../KohakuMossTrainer), built as a **separate
package over the trainer's CLI and file contract**. It never imports the trainer, never edits
its source, and survives upstream merges: the whole integration lives in `contract/`.

```
frontend (static ES modules)
        |  HTTP + SSE, 127.0.0.1 + one-time token
backend (stdlib http.server)
        |  kogine run / SIGTERM        |  metrics.jsonl, log.txt, checkpoints/
   trainer process                   run directory
```

## Version 1.0

This is the 1.0 release of the UI.

**Windows support is not fully tested.** The UI itself and its tool discovery, process
handling and encodings were hardened for Windows, but the grabber backend still contains
POSIX assumptions and no real Windows run of the full flow has been done. Issues are welcome.

Issues and pull requests are welcome. Please include the OS, the Python version and the
relevant log output.

## Interface reference

[接口参考（中文）](docs/interfaces.md) covers the current HTTP routes, SSE events,
CLI options, trainer file/process contract, and local security limits. Historical
plans under `docs/plans/` are not the current API specification.

## Install

Use the trainer's own virtualenv so the UI runs on the same Python and sees `kogine`:

```bash
cd ../KohakuMoss-UI
uv pip install -e .            # or: ../KohakuMossTrainer/.venv/bin/pip install -e .
# optional native window (pywebview)
uv pip install -e ".[native]"
```

No third-party runtime dependency is required for the browser mode. The bundled IBM Plex Mono
subset (OFL) keeps metrics identical on Windows, Linux and macOS.

## Run

```bash
kohakumoss-ui --root ../KohakuMossTrainer
kohakumoss-ui --root ../KohakuMossTrainer --native     # pywebview window
kohakumoss-ui --root ../KohakuMossTrainer --no-open
kohakumoss-ui --root ../KohakuMossTrainer --runs /path/to/runs   # inspect another output root
```

It prints the URL with the token, for example:

```
kohakumoss-ui KohakuMossTrainer 0.1.0
  root   /path/KohakuMossTrainer
  runs   /path/KohakuMossTrainer/runs/moss
  open   http://127.0.0.1:41233/?token=...
```

The root is discovered from `--root`, `KOHAKUMOSS_ROOT`, the current directory, or a sibling
`KohakuMossTrainer` checkout. `KOHAKUMOSS_RUNS` and `KOHAKUMOSS_PYTHON` override the runs root
and interpreter.

## ArtistSourceGrabber integration

The collection workspace uses the same UI shell and a dedicated backend adapter,
not the Grabber's old page. Start the UI normally, then explicitly start the
collection service from the workspace. No source login, model load or download
runs automatically. See [采集接入与 API / lifecycle](docs/grabber.md).

The default checkout is the trainer's sibling `ArtistSourceGrabber`; override it
with `KOHAKUMOSS_GRABBER_ROOT`. `KOHAKUMOSS_GRABBER_PYTHON` overrides its interpreter
(default: the Grabber's `.venv`, then the current Python). Dependencies must already
be installed. This adapter currently targets POSIX. Credentials are private local
UI state files and are never returned as plaintext secrets by the API.

**Closing the UI process leaves its Grabber process and collection running.**
Record the status `pid` / `port` first: a restarted UI does not adopt the old service
or recover task history. Stop remains cooperative; wait for a terminal task status
before explicitly shutting down the original ASG service. Full details and the
local-only security boundary are in the integration guide above.

## What works

| page | reads / writes |
| --- | --- |
| Runs | scans `runs/moss/*`, metrics and log tails, managed pid |
| New run | AST-scans 143 knobs (still growing upstream), loads a config through `kohakuengine`, sends only edits as `--set` |
| Datasets | `DATASETS` from configs, image/caption counts, `cache/` presence |
| Artifacts | checkpoints, exports, sample thumbnails and `grid.png` |
| Doctor | `kohakumoss-doctor --json`, cached 60 s, plus a preflight from user configs |
| Settings | theme family, light/carbon, appearance (wallpaper + glass), UI scale (100–140 %, default 110 %), density, discovered paths |

Run control is deliberately small: start is `kogine run <config>` in its own process group,
stop is SIGTERM (the trainer unwinds through `SystemExit(143)`), and resume is
`RESUME=latest`. There is no pause because the trainer has none.

## Themes and appearance

Three theme families live in `web/themes/`, each with a light and a carbon mode:

| theme | light | carbon |
| --- | --- | --- |
| Kohaku | warm gray paper, gem accents | warm charcoal |
| Soul | cool neutral instrument | dark forge bench (default) |
| Teletype | receipt paper | solid graphite |

Each theme keeps its own colour overrides. In SETTINGS → THEME the five pickers (`canvas`,
`panel`, `text`, `border`, `accent`) sit right under MODE and apply to the active theme only.
An empty value means "use the theme value"; secondary, muted, disabled and border shades are
derived with `color-mix()`, and the panel shows live WCAG contrast for text/canvas and
accent/canvas. `RESET` drops the overrides of the active theme.

The appearance layer is **independent of the theme** and applies to all three:

- glass: the wallpaper is one paint on the `<html>` canvas; surface alpha comes from a single
  strength value and thins **the controls only** 1:1 (the page fill belongs to the wallpaper),
  and `backdrop-filter` stays on the sidebar, header and footer only — per-card
  sampling is costly and can blank out content on some drivers (the MonadForge approach).
  No panel clamps the slider with a readability floor; the background mask/veil keeps text
  legible instead. Teletype reads the same value 1:1 and skips `backdrop-filter`
  entirely (see below).
- one custom background image: upload (png/jpeg/webp, max 25 MB) or paste an `https://` /
  `data:image/` URL
- image blur (0–30 px, default 5) is baked into the image on a canvas, never applied as a
  full-viewport CSS filter; the mask (default 50 %) is a theme-coloured veil painted with it

Teletype is the exception in material, not in controls: its **controls are paper** (vellum) instead of
frosted glass, while the page keeps the wallpaper. Glass and surface opacity apply exactly as they do
elsewhere — the slider thins each control 1:1 — but `backdrop-filter` stays banned because the blur is
already baked into the wallpaper; the punch band stays solid, every control thins 1:1 with no
alpha floor, and a paper veil holds body-text contrast at the low end. Switching glass, opacity or the
wallpaper never changes geometry: there is no desk mat, and the photo only shows between the controls.
The full language, token ownership and the static gate live in `docs/teletype.md`.

State is stored in `appearance.json` under the UI state directory, and the single uploaded
image is `backgrounds/custom.<ext>` next to it. Uploads are checked by MIME type, magic bytes
and, when Pillow happens to be installed, `Image.verify()`. Nothing reads the desktop
wallpaper, no colour is sampled from the image, and no system service is contacted.


## Architecture rules (keep this out of the swamp)

1. `contract/` is the only place that knows trainer names: `kogine`, `metrics.jsonl`, knob
   names, run layout, doctor JSON. Everything else consumes plain JSON models.
2. Upstream changes are handled in `contract/` and its tests, not by patching the trainer.
3. The frontend consumes semantic CSS variables; only `themes/*.css` may define colours.
4. Server state lives under `~/.local/state/kohakumoss-ui/` (override with `--state` or
   `KOHAKUMOSS_UI_STATE`), never inside a run directory.

## Security

- Binds `127.0.0.1` by default, picks a free port, and rejects API calls without the token.
- The token is kept in the tab's session storage; static assets carry no secrets.
- The server can start and stop training processes. Do not bind it to a public interface.

## Tests

```bash
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests -v
```

The suite covers the contract layer only (AST scan, metrics tail, log offsets, run summary,
path guard); it never imports torch.

## Demo data

`tools/make_demo_run.py` (stdlib only) writes a fake run directory — `config.json`,
`metrics.jsonl`, `log.txt`, `checkpoints/step_XXXXXX/`, `samples/step_XXXXXX/` and
`samples/grid.png` — so the Runs page and the run detail can be previewed without training:

```bash
python tools/make_demo_run.py --out /tmp/kohakumoss-demo/runs/moss --name demo-done --steps 2000
python tools/make_demo_run.py --out /tmp/kohakumoss-demo/runs/moss --name demo-running \
  --steps 2000 --at-step 1800 --ended-ago 0        # still in progress
kohakumoss-ui --root /tmp/kohakumoss-demo --state /tmp/kmstate-demo --token tb
```

`--at-step` stops the metrics below `STEPS` (unfinished run), `--ended-ago` ages the file
mtimes. A run only reads as **RUNNING** through the managed pid, so register a live pid for
the demo run in `/tmp/kmstate-demo/runs.json` (`{"runs": {"demo-running": {"pid": <pid>,
"config": "...", "command": [...], "log_path": "...", "started_at": <epoch>}}}`). Keep every
demo artifact under `/tmp`; never point this at `~/.local/state/kohakumoss-ui/`.

## Not done yet

- System wallpaper import, desktop colour sampling and auto palettes are deliberately out of
  scope: the wallpaper is an uploaded file or URL only.
- Creating configs from the UI (use `kohakumoss-config new` for now).
- Bundled desktop binaries / Tauri shell. The HTTP contract makes a shell replaceable.
- Windows stop uses `taskkill /T`; graceful SIGTERM semantics exist on POSIX only.
