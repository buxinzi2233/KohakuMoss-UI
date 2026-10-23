#!/usr/bin/env python3
"""Write a fake run directory so the UI can be previewed without training.

The layout it produces is the one ``contract/runs.py`` scans:

    <out>/<name>/config.json
    <out>/<name>/metrics.jsonl
    <out>/<name>/log.txt
    <out>/<name>/checkpoints/step_XXXXXX/step-XXXXXX.safetensors
    <out>/<name>/samples/step_XXXXXX/{grid,01,02}.png
    <out>/<name>/samples/grid.png

Standard library only. Nothing here touches a real trainer or its runs.

Examples:
    python tools/make_demo_run.py --out /tmp/kohakumoss-demo/runs/moss --name demo-running
    python tools/make_demo_run.py --out /tmp/kohakumoss-demo/runs/moss --name demo-done \\
        --at-step 2000 --ended-ago 7200
"""

import argparse
import json
import math
import os
import random
import struct
import sys
import time
import zlib
from pathlib import Path

METRIC_EVERY = 1  # one metrics row per step, so the chart is dense
LOG_EVERY = 10  # the trainer prints a progress line every LOG_EVERY steps
SAVE_EVERY = 500
SAMPLE_EVERY = 250
EPOCH_STEPS = 400  # synthetic epoch length, only used for the "epoch" field


# --------------------------------------------------------------------------- #
# pixel and png helpers (no Pillow)
# --------------------------------------------------------------------------- #
def _png_gray(size, pixels):
    """Encode an 8-bit grayscale image from a flat list of 0..255 values."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0 (none)
        raw.extend(pixels[y * size:(y + 1) * size])

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _sample_pixels(size, seed):
    """Soft grayscale blobs on a vignetted ground; deterministic per seed."""
    rng = random.Random(seed)
    blobs = [
        (rng.uniform(0.12, 0.88), rng.uniform(0.12, 0.88), rng.uniform(0.10, 0.30), rng.uniform(0.45, 0.95))
        for _ in range(4)
    ]
    out = bytearray(size * size)
    last = size - 1
    for y in range(size):
        ty = y / last
        for x in range(size):
            tx = x / last
            value = 0.09 + 0.30 * (1.0 - min(1.0, abs(tx - 0.5) * 1.5)) * (1.0 - min(1.0, abs(ty - 0.5) * 1.2))
            for bx, by, br, weight in blobs:
                dist = (tx - bx) ** 2 + (ty - by) ** 2
                value += weight * math.exp(-dist / (2.0 * br * br))
            value += 0.08 * max(0.0, math.sin((tx * 9.0 + ty * 4.0) * math.pi))
            value = max(0.0, value)
            out[y * size + x] = max(0, min(255, int(round(235.0 * value / (1.0 + value)))))
    return bytes(out)


def write_sample_png(path, size, seed):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_gray(size, _sample_pixels(size, seed)))


def write_fake_safetensors(path, step):
    """A tiny but structurally plausible safetensors file (well under 4 KiB)."""
    header = {
        "__metadata__": {"format": "pt", "kohakumoss_demo": "true", "step": str(step)},
        "lora_unet_down_blocks_0_attentions_0_transformer_blocks_0_attn1_to_q.lora_down.weight": {
            "dtype": "F16",
            "shape": [16, 8],
            "data_offsets": [0, 256],
        },
        "lora_unet_down_blocks_0_attentions_0_transformer_blocks_0_attn1_to_q.lora_up.weight": {
            "dtype": "F16",
            "shape": [8, 16],
            "data_offsets": [256, 512],
        },
    }
    blob = json.dumps(header, separators=(",", ":")).encode("utf-8")
    blob += b" " * ((8 - len(blob) % 8) % 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + bytes(512))


# --------------------------------------------------------------------------- #
# curves
# --------------------------------------------------------------------------- #
def lr_at(step, steps, base):
    warmup = 50
    if step < warmup:
        return base * (step + 1) / warmup
    progress = (step - warmup) / float(max(1, steps - warmup))
    return base * (0.05 + 0.95 * 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress))))


def loss_at(step, steps, rng):
    base = 0.262 + 0.735 * math.exp(-step / 1100.0)
    base += 0.02 * math.sin(step / 90.0)
    point = base + rng.gauss(0.0, 0.011)
    if rng.random() < 0.02:
        point *= 1.0 + rng.uniform(0.06, 0.22)  # occasional batch spike
    return max(0.05, point)


def metrics_rows(steps, at_step, start_time, step_time):
    rng = random.Random(20240517)
    rows = []
    for step in range(METRIC_EVERY, at_step + 1, METRIC_EVERY):
        warm = min(1.0, step / 40.0)
        alloc = 13.4 + 1.35 * warm + rng.gauss(0.0, 0.04)
        tokens = (1500.0 + 1180.0 * min(1.0, step / 30.0)) + rng.gauss(0.0, 95.0)
        grad = 0.62 + 1.45 * math.exp(-step / 1500.0) + abs(rng.gauss(0.0, 0.13))
        if rng.random() < 0.015:
            grad *= rng.uniform(2.5, 4.2)  # clipped spike, see log warnings
        rows.append(
            {
                "step": step,
                "epoch": round(step / EPOCH_STEPS, 4),
                "loss": round(loss_at(step, steps, rng), 5),
                "lr": float("%.9f" % lr_at(step, steps, 0.0001)),
                "grad_norm": round(grad, 4),
                "tokens_per_s": round(tokens, 1),
                "vram_peak_alloc": round(alloc, 2),
                "vram_peak_reserved": round(alloc + 1.18, 2),
                "step_time": round(step_time + rng.gauss(0.0, 0.022) + (0.9 if step < 20 else 0.0), 4),
                "time": round(start_time + step * step_time, 3),
            }
        )
    return rows


def log_lines(steps, at_step, rows, start_time, step_time, name, finished):
    """Trainer-ish log: HH:MM:SS LEVEL message, as format.js parses it."""
    rng = random.Random(99)
    lines = []
    clock = start_time
    total_seconds = int(at_step * step_time)

    def stamp(moment):
        return time.strftime("%H:%M:%S", time.localtime(moment))

    def add(moment, level, message):
        lines.append("%s %s %s" % (stamp(moment), level, message))

    add(clock, "INFO", "kohakumoss-trainer 0.4.2 | run %s" % name)
    add(clock, "INFO", "config configs/user/demo_lora.toml resolved to 143 knobs")
    add(clock, "INFO", "data 1240 images / 1240 captions from assets/demo-set")
    add(clock, "WARNING", "text cache miss on first pass; encoding 1240 captions takes ~40 s")
    add(clock, "INFO", "model variant=base weights=moss_bf16.safetensors dtype=bf16")
    add(clock, "INFO", "adapter algo=lora dim=16 alpha=16 applied to 96 modules")
    add(clock, "WARNING", "memory profile 'balanced' caps micro-batch at 2")
    add(clock, "INFO", "optimizer adamw lr=0.0001 wd=0.01 sched=cosine grad_clip=1.0")
    add(clock, "INFO", "train start steps=%d grad_acc=4 seed=1234" % steps)
    clock += 2.6

    last_epoch = 0
    for row in rows:
        step = row["step"]
        clock = start_time + step * step_time
        if step % EPOCH_STEPS == 0:
            last_epoch += 1
            add(clock, "INFO", "epoch %d/%d complete in %s | loss %.4f" % (
                last_epoch, int(math.ceil(steps / float(EPOCH_STEPS))),
                time.strftime("%M:%S", time.gmtime(step * step_time)), row["loss"]))
        if step % LOG_EVERY == 0:
            add(clock, "INFO", "step %d/%d loss %.4f lr %.2e grad_norm %.3f %.1f tok/s %.2fs" % (
                step, steps, row["loss"], row["lr"], row["grad_norm"],
                row["tokens_per_s"], row["step_time"]))
            if row["grad_norm"] > 2.6:
                add(clock + 0.4, "WARNING", "grad_norm %.2f above clip 1.00, scaler rescaled" % row["grad_norm"])
            if step % 600 == 0:
                add(clock + 0.7, "WARNING", "latent cache hit rate %.1f%%, retrying %d tiles" % (
                    rng.uniform(88.0, 96.0), rng.randint(1, 4)))
        if step % SAVE_EVERY == 0:
            add(clock + 1.4, "INFO", "checkpoint saved checkpoints/step_%06d (2 files, 41.2 MiB)" % step)
        if step % SAMPLE_EVERY == 0:
            add(clock + 2.1, "INFO", "sample 4 prompts rendered samples/step_%06d/grid.png in %.2fs" % (
                step, rng.uniform(1.4, 2.4)))

    if finished:
        add(start_time + total_seconds + 3.2, "INFO", "run finished in %s | best loss %.4f" % (
            time.strftime("%H:%M:%S", time.gmtime(total_seconds)), min(row["loss"] for row in rows)))
        add(start_time + total_seconds + 3.4, "INFO", "adapter exported to export/kohakumoss_adapter.safetensors")
    else:
        add(start_time + total_seconds + 1.0, "INFO", "still training, next checkpoint at step %d" % (
            (at_step // SAVE_EVERY + 1) * SAVE_EVERY))
    return lines


# --------------------------------------------------------------------------- #
# run assembly
# --------------------------------------------------------------------------- #
def build_config(name, steps, out_root):
    return {
        "RUN_NAME": name,
        "WEIGHTS": "models/moss_bf16.safetensors",
        "VARIANT": "base",
        "TEXT_ENCODER": "models/qwen3_text_encoder.safetensors",
        "VAE": "models/moss_vae.safetensors",
        "DATASETS": ["assets/demo-set"],
        "RESOLUTION_MODE": "bucket",
        "TARGET_AREA": 1024 * 1024,
        "BATCHING": "tokens",
        "MAX_TOKENS": 4096,
        "OVERLONG": "truncate_text",
        "LATENT_CACHE": "online",
        "TEXT_CACHE": "online",
        "ALGO": "lora",
        "PRESET": "balanced",
        "DIM": 16,
        "ALPHA": 16,
        "ADAPTER_MODE": "bypass",
        "OPTIMIZER": "adamw",
        "LR": 0.0001,
        "BETAS": [0.9, 0.99],
        "WEIGHT_DECAY": 0.01,
        "SCHEDULE": "cosine",
        "GRAD_CLIP": 1.0,
        "STEPS": steps,
        "GRAD_ACC": 4,
        "SEED": 1234,
        "PRECISION": "bf16",
        "MEMORY_PROFILE": "balanced",
        "CHECKPOINT": True,
        "SAVE_EVERY": SAVE_EVERY,
        "SAVE_FORMAT": "comfy",
        "SAMPLE_PROMPTS": "configs/prompts/demo.json",
        "SAMPLE_EVERY": SAMPLE_EVERY,
        "LOSS": "mse",
        "TIMESTEP": "moss",
        "OUTPUT_DIR": str(out_root),
        "LOGGER": "console",
    }


def make_run(out_root, name, steps, at_step, ended_ago, sample_size):
    run = Path(out_root) / name
    run.mkdir(parents=True, exist_ok=True)
    step_time = 0.42
    end_time = time.time() - ended_ago
    start_time = end_time - at_step * step_time
    finished = at_step >= steps

    rows = metrics_rows(steps, at_step, start_time, step_time)
    config = build_config(name, steps, out_root)
    (run / "config.json").write_text(json.dumps(config, indent=2) + "\n", "utf-8")
    with (run / "metrics.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    lines = log_lines(steps, at_step, rows, start_time, step_time, name, finished)
    (run / "log.txt").write_text("\n".join(lines) + "\n", "utf-8")

    checkpoints = list(range(SAVE_EVERY, at_step + 1, SAVE_EVERY))
    for step in checkpoints:
        folder = run / "checkpoints" / ("step_%06d" % step)
        write_fake_safetensors(folder / ("step-%06d.safetensors" % step), step)
    if checkpoints:
        newest = run / "checkpoints" / ("step_%06d" % checkpoints[-1])
        export = newest / "export"
        export.mkdir(parents=True, exist_ok=True)
        write_fake_safetensors(export / "kohakumoss_adapter.safetensors", checkpoints[-1])
        (export / "adapter_config.json").write_text(
            json.dumps({"algo": "lora", "dim": 16, "alpha": 16, "step": checkpoints[-1]}, indent=2) + "\n",
            "utf-8",
        )
    if finished:
        run_export = run / "export"
        run_export.mkdir(parents=True, exist_ok=True)
        write_fake_safetensors(run_export / "kohakumoss_adapter.safetensors", at_step)
        (run_export / "README.txt").write_text(
            "Demo export written by tools/make_demo_run.py; not a real adapter.\n", "utf-8"
        )

    sample_steps = list(range(SAMPLE_EVERY, at_step + 1, SAMPLE_EVERY))
    for step in sample_steps:
        folder = run / "samples" / ("step_%06d" % step)
        write_sample_png(folder / "grid.png", sample_size, seed=step)
        write_sample_png(folder / "01.png", sample_size, seed=step + 1)
        write_sample_png(folder / "02.png", sample_size, seed=step + 2)
    if not sample_steps:
        sample_steps = [at_step]
    write_sample_png(run / "samples" / "grid.png", sample_size, seed=at_step)

    # Touch every artifact forward in time so the ledger shows realistic activity.
    for step in checkpoints:
        folder = run / "checkpoints" / ("step_%06d" % step)
        moment = start_time + step * step_time
        for item in sorted(folder.rglob("*")) + [folder]:
            _set_mtime(item, moment)
    for step in sample_steps:
        folder = run / "samples" / ("step_%06d" % step)
        moment = start_time + step * step_time
        for item in sorted(folder.rglob("*")) + [folder]:
            _set_mtime(item, moment)
    for item in (run / "samples").iterdir():
        if item.is_file():
            _set_mtime(item, start_time + at_step * step_time)
    for name_ in ("config.json", "metrics.jsonl", "log.txt"):
        _set_mtime(run / name_, end_time)
    if finished and (run / "export").is_dir():
        for item in sorted((run / "export").rglob("*")) + [run / "export"]:
            _set_mtime(item, end_time)
    _set_mtime(run, end_time)
    _set_mtime(run / "checkpoints", start_time + (checkpoints[-1] if checkpoints else at_step) * step_time)

    return {
        "run": run,
        "metrics": len(rows),
        "logs": len(lines),
        "checkpoints": len(checkpoints),
        "samples": len(sample_steps),
        "last_step": at_step,
        "steps": steps,
    }


def _set_mtime(path, moment):
    try:
        os.utime(path, (moment, moment))
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="make_demo_run.py",
        description="Generate a fake KohakuMoss run directory for UI previews",
    )
    parser.add_argument("--out", default="/tmp/kohakumoss-demo/runs/moss",
                        help="runs root that receives the run directory (default: /tmp/kohakumoss-demo/runs/moss)")
    parser.add_argument("--name", default="demo-run", help="run directory name (default: demo-run)")
    parser.add_argument("--steps", type=int, default=2000, help="configured STEPS (default: 2000)")
    parser.add_argument("--at-step", type=int, default=0,
                        help="last step recorded in metrics.jsonl (default: --steps, i.e. finished)")
    parser.add_argument("--ended-ago", type=float, default=0.0,
                        help="seconds between the last metrics row and now (default: 0)")
    parser.add_argument("--sample-size", type=int, default=256, help="sample PNG edge in pixels (default: 256)")
    args = parser.parse_args(argv)

    steps = max(100, args.steps)
    at_step = steps if args.at_step <= 0 else min(args.at_step, steps)
    at_step = max(METRIC_EVERY, at_step)

    info = make_run(args.out, args.name, steps, at_step, args.ended_ago, max(64, args.sample_size))
    run = info["run"]
    grid = run / "samples" / "grid.png"
    finished = at_step >= steps

    print("demo run   %s" % run)
    print("config     %s  (ALGO=lora DIM=16 LR=0.0001 STEPS=%d)" % (run / "config.json", steps))
    print("metrics    %s  %d lines, last step %d/%d%s" % (
        run / "metrics.jsonl", info["metrics"], at_step, steps,
        "" if finished else " (still in progress)"))
    print("log        %s  %d lines" % (run / "log.txt", info["logs"]))
    print("checkpoints %s  %d dirs (step_%06d ...)" % (
        run / "checkpoints", info["checkpoints"], SAVE_EVERY if info["checkpoints"] else 0))
    print("samples    %s  %d steps, samples/grid.png %d bytes" % (
        run / "samples", info["samples"], grid.stat().st_size if grid.is_file() else 0))
    print("status     %s" % ("metrics reach STEPS -> 'done'" if finished else
                            "metrics stop below STEPS -> 'stopped' unless a managed pid is alive"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
