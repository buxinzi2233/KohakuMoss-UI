"""Command line entry point for the KohakuMoss UI."""

import argparse
import os
import secrets
import sys
import threading

from . import paths
from .contract import discover
from .server.http import make_server
from .shell import browser, native
from .store import queue


QUEUE_INTERVAL = 5.0


def start_scheduler(root, stop: threading.Event, interval: float = QUEUE_INTERVAL) -> threading.Thread:
    """Tick the queue in the background so waiting items eventually start."""

    def loop() -> None:
        while not stop.is_set():
            try:
                queue.tick(root)
            except Exception:
                pass
            if stop.wait(interval):
                return

    thread = threading.Thread(target=loop, name="km-queue", daemon=True)
    thread.start()
    return thread


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kohakumoss-ui",
        description="Local workbench for the KohakuMoss trainer",
    )
    parser.add_argument("--root", default="", help="trainer checkout (default: discover)")
    parser.add_argument("--runs", default="", help="run output root (default: <root>/runs/moss)")
    parser.add_argument("--state", default="", help="UI state directory")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=0, help="port, 0 picks a free one")
    parser.add_argument("--token", default="", help="fixed access token")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument("--native", action="store_true", help="open a pywebview window")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.runs:
        os.environ["KOHAKUMOSS_RUNS"] = args.runs
    if args.state:
        os.environ["KOHAKUMOSS_UI_STATE"] = args.state

    try:
        root = discover(args.root or None)
    except RuntimeError as exc:
        print(f"kohakumoss-ui: {exc}", file=sys.stderr)
        return 2

    paths.state_dir()
    try:
        queue.tick(root)
    except Exception:
        pass
    stop_scheduler = threading.Event()
    start_scheduler(root, stop_scheduler)

    token = args.token or secrets.token_urlsafe(18)
    server = make_server(root, args.host, args.port, token)
    port = server.server_address[1]
    host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{host}:{port}/?token={token}"

    print(f"kohakumoss-ui {root.root.name} {root.version}")
    print(f"  root   {root.root}")
    print(f"  runs   {root.runs_dir}")
    print(f"  open   {url}")
    print("  stop   Ctrl+C")

    try:
        if args.native:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                native.open_window(url)
            finally:
                server.shutdown()
        else:
            if not args.no_open:
                browser.open_url(url)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_scheduler.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
