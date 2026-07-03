"""Command-line interface (argparse).

    sentinel run     [-c config.json]              # continuous monitoring loop
    sentinel check   [-c config.json] [--json]     # one-shot health, exit 0/1/2
    sentinel status  [-c config.json] [-o page.html]
    sentinel serve   [-c config.json] [--port 8787]  # monitor loop + HTTP status page
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .engine import Engine
from .report import render_html, render_json, render_text
from .serve import serve as build_server

_DEFAULT_CONFIG = "sentinel.config.json"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sentinel",
        description="Self-hosted uptime / TLS monitor with Telegram alerts.",
    )
    p.add_argument("--version", action="version", version=f"sentinel {__version__}")
    sub = p.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="continuous monitoring loop (debounced alerts)")
    run_p.add_argument("-c", "--config", default=_DEFAULT_CONFIG)

    check_p = sub.add_parser("check", help="one-shot health check; exit 0=up 1=degraded 2=down")
    check_p.add_argument("-c", "--config", default=_DEFAULT_CONFIG)
    check_p.add_argument("--json", action="store_true", help="print the snapshot as JSON")

    status_p = sub.add_parser("status", help="probe once and render the HTML status page")
    status_p.add_argument("-c", "--config", default=_DEFAULT_CONFIG)
    status_p.add_argument("-o", "--output", help="write HTML here (default: config status_page, else stdout)")

    serve_p = sub.add_parser("serve", help="run the monitor loop and serve the status page over HTTP")
    serve_p.add_argument("-c", "--config", default=_DEFAULT_CONFIG)
    serve_p.add_argument("--port", type=int, default=8787, help="HTTP port to listen on (default: 8787)")
    serve_p.add_argument("--host", default="", help="bind address (default: all interfaces)")

    return p


def _run_serve(engine: Engine, host: str, port: int) -> int:
    """Loop ``engine.tick()`` in a daemon thread and serve the status page.

    The HTTP server runs in the foreground; each request renders a fresh
    snapshot, so the page always reflects the latest tick. Ctrl+C stops both.
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            engine.tick()
            stop.wait(engine.settings.interval_seconds)

    worker = threading.Thread(target=loop, name="sentinel-monitor", daemon=True)
    worker.start()

    server = build_server(engine.snapshot, host=host, port=port)
    shown_host = host or "0.0.0.0"
    print(f"sentinel serving on http://{shown_host}:{port} "
          f"(/, /status.json, /health), watching {len(engine.settings.targets)} "
          f"target(s) every {engine.settings.interval_seconds}s. Ctrl+C to stop.",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped.", flush=True)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.command:
        _build_parser().print_help()
        return 0

    try:
        settings = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    engine = Engine(settings)

    if args.command == "run":
        engine.run()
        return 0

    if args.command == "check":
        code = engine.check_once()
        snapshot = engine.snapshot()
        print(render_json(snapshot) if args.json else render_text(snapshot))
        return code

    if args.command == "status":
        engine.check_once()
        html_doc = render_html(engine.snapshot())
        out = args.output or settings.status_page
        if out:
            Path(out).write_text(html_doc, encoding="utf-8")
            print(f"wrote status page to {out}")
        else:
            print(html_doc)
        return 0

    if args.command == "serve":
        return _run_serve(engine, args.host, args.port)

    return 0
