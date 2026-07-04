"""TCP-connection honeypot sensor (standard library only).

The "watchtower" sensor watches *your* services; this one watches the *attackers*.
It binds a low-interaction listener (e.g. on the SSH port) that never grants
access — it just records who connected, when, and the first bytes / client
banner they sent, then emits events into the same sentinel core (store + alerter
+ status page). It realises sentinel's second sensor without any dependency:
just ``socket`` / ``socketserver`` / ``threading``.

It fits the poll-based engine: a background listener thread buffers connection
hits; ``poll()`` drains the buffer into events each tick. Every hit is an INFO
event (recorded, shown on the status page, not alerted); the first sighting of a
source IP within the re-alert window is a WARNING (alerted), and a burst of many
new sources is aggregated into a single summary alert so a scan can't spam you.
"""

from __future__ import annotations

import socketserver
import sys
import threading
import time

from ..models import Event, Severity
from . import Sensor


class HoneypotSensor(Sensor):
    name = "honeypot"

    _MAX_BUFFER = 2000  # cap buffered hits so an unpolled sensor can't grow unbounded

    def __init__(self, host: str = "", port: int = 2222, banner: str = "",
                 realert_seconds: int = 3600) -> None:
        self.host = host or ""
        self.port = int(port)
        self.banner = banner or ""
        self._realert_seconds = realert_seconds
        self._lock = threading.Lock()
        self._hits: list[dict] = []
        self._seen: dict[str, float] = {}  # ip -> last time we alerted on it
        self._server = None
        self._started = False
        self._active = False
        self.bound_port = None  # actual port (useful when port=0 in tests)

    # -- listener lifecycle --------------------------------------------------
    def _ensure_started(self) -> None:
        """Bind + serve on first poll (so `check`/`status` never open a port)."""
        if self._started:
            return
        self._started = True
        try:
            server = socketserver.ThreadingTCPServer((self.host, self.port), self._make_handler())
            server.allow_reuse_address = True
            server.daemon_threads = True
        except OSError as exc:
            print(f"honeypot: could not bind {self.host or '0.0.0.0'}:{self.port}: {exc}",
                  file=sys.stderr, flush=True)
            self._active = False
            return
        self._server = server
        self.bound_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, name="sentinel-honeypot",
                         daemon=True).start()
        self._active = True
        print(f"honeypot listening on {self.host or '0.0.0.0'}:{self.bound_port}",
              file=sys.stderr, flush=True)

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._active = False

    def _make_handler(self):
        sensor = self
        banner = self.banner

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self):  # noqa: ANN001
                ip = self.client_address[0]
                ts = time.time()
                data = b""
                try:
                    self.request.settimeout(2.0)
                    if banner:  # look like a real service to draw out a client banner
                        try:
                            self.request.sendall((banner + "\r\n").encode())
                        except Exception:
                            pass
                    data = self.request.recv(256)
                except Exception:
                    pass
                sensor._add_hit(ip, ts, data.decode("latin-1", "replace").strip())

        return _Handler

    def _add_hit(self, ip: str, ts: float, banner: str) -> None:
        with self._lock:
            if len(self._hits) < self._MAX_BUFFER:
                self._hits.append({"ip": ip, "ts": ts, "banner": banner})

    # -- poll ----------------------------------------------------------------
    def poll(self) -> list[Event]:
        self._ensure_started()
        if not self._active:
            return []
        with self._lock:
            hits, self._hits = self._hits, []
        return self._events_for_hits(hits, time.time())

    def _events_for_hits(self, hits: list[dict], now: float) -> list[Event]:
        """Pure: buffered hits -> events. INFO per hit; a WARNING for each new
        source (aggregated into one summary when there are many). Updates _seen.
        """
        events: list[Event] = []
        new_sources: list[tuple[str, str, float]] = []
        for h in hits:
            ip = h["ip"]
            banner = h.get("banner") or ""
            events.append(Event(
                "honeypot_hit", Severity.INFO, "honeypot",
                f"connection from {ip}",
                banner[:120] if banner else "(no banner)", h["ts"],
            ))
            last = self._seen.get(ip)  # None = never seen -> always a new source
            if last is None or now - last > self._realert_seconds:
                new_sources.append((ip, banner, h["ts"]))
            self._seen[ip] = now

        if new_sources:
            if len(new_sources) <= 5:
                for ip, banner, ts in new_sources:
                    events.append(Event(
                        "intrusion", Severity.WARNING, "honeypot",
                        f"honeypot: connection from {ip}",
                        f"client banner: {banner[:120]}" if banner else "no banner", ts,
                    ))
            else:  # a scan/burst: one summary alert instead of one-per-IP
                sample = ", ".join(ip for ip, _, _ in new_sources[:10])
                if len(new_sources) > 10:
                    sample += " …"
                events.append(Event(
                    "intrusion", Severity.WARNING, "honeypot",
                    f"honeypot: {len(new_sources)} new sources ({len(hits)} hits)",
                    sample, now,
                ))
        return events
