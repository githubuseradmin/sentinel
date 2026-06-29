"""Command-line interface (argparse).

    sentinel run     [-c config.json]              # continuous monitoring loop
    sentinel check   [-c config.json] [--json]     # one-shot health, exit 0/1/2
    sentinel status  [-c config.json] [-o page.html]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .engine import Engine
from .report import render_html, render_json, render_text

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

    return p


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

    return 0
