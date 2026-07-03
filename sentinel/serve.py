"""Serve the status page over stdlib ``http.server`` (no dependencies).

The routing itself — mapping a request path to a response — is a *pure*
function (:func:`route`) that takes a snapshot dict and a path and returns a
``(status_code, content_type, body)`` tuple. It never touches a socket, so it is
trivial to unit-test. The HTTP plumbing (:func:`serve`) just wires that pure
function to a ``ThreadingHTTPServer`` and asks a callback for a fresh snapshot on
each request.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .report import render_html, render_json

# Paths that return the machine-readable JSON snapshot.
_JSON_PATHS = {"/status.json", "/health"}

_NOT_FOUND_BODY = "404 Not Found\n"


def route(snapshot: dict, path: str) -> tuple[int, str, str]:
    """Map a request path to ``(status_code, content_type, body)``.

    Pure and socket-free so it can be unit-tested directly:

    * ``/``                       -> 200, the rendered HTML status page
    * ``/status.json`` / ``/health`` -> 200, the JSON snapshot
    * anything else               -> 404, a tiny plain-text body

    A query string (``/status.json?foo=bar``) is ignored when matching.
    """
    clean = path.split("?", 1)[0]
    if clean == "/":
        return 200, "text/html; charset=utf-8", render_html(snapshot)
    if clean in _JSON_PATHS:
        return 200, "application/json; charset=utf-8", render_json(snapshot)
    return 404, "text/plain; charset=utf-8", _NOT_FOUND_BODY


def _make_handler(snapshot_fn: Callable[[], dict]) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to a snapshot provider."""

    class StatusHandler(BaseHTTPRequestHandler):
        server_version = "sentinel"

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            status, content_type, body = route(snapshot_fn(), self.path)
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args) -> None:  # noqa: D401 — silence default noise
            """Suppress the default one-line-per-request stderr logging."""

    return StatusHandler


def serve(snapshot_fn: Callable[[], dict], host: str = "", port: int = 8787) -> ThreadingHTTPServer:
    """Create (but do not start) a ``ThreadingHTTPServer`` for the status page.

    ``snapshot_fn`` is called per request to obtain a fresh snapshot, so the page
    always reflects the latest monitor state. The caller is responsible for
    ``serve_forever()`` / ``shutdown()`` — the CLI runs it in the foreground.
    """
    return ThreadingHTTPServer((host, port), _make_handler(snapshot_fn))
