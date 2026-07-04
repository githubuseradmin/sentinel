"""The monitor engine: drives sensors, persists events, alerts, and reports.

Two modes share one core:

* ``run``        — continuous loop; sensors are *debounced* so alerts fire on
  state transitions, not on every flap. INFO events are logged but not alerted.
* ``check_once`` — a point-in-time probe for cron/CI; returns an exit code from
  the current health (0 up, 1 degraded, 2 down). No debounce: it reports what is
  true right now.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .alerts import build_alerter
from .checks import run_check
from .config import Settings
from .models import Severity, Status
from .report import render_html
from .sensors.uptime import UptimeSensor
from .store import Store

_STATUS_ORDER = ("down", "degraded", "unknown", "up")
_EXIT_CODE = {"up": 0, "unknown": 0, "degraded": 1, "down": 2}


class Engine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.alerter = build_alerter(settings)
        self.uptime = UptimeSensor(settings.targets, self.store)
        self.sensors = [self.uptime]
        # Optional intrusion sensor. Constructed cheaply here; it only binds its
        # port on the first poll(), so `check`/`status` never open a listener.
        self.honeypot = None
        if settings.honeypot_enabled:
            from .sensors.honeypot import HoneypotSensor

            self.honeypot = HoneypotSensor(
                settings.honeypot_host,
                settings.honeypot_port,
                settings.honeypot_banner,
            )
            self.sensors.append(self.honeypot)

    # -- continuous mode -----------------------------------------------------
    def tick(self) -> None:
        """One monitoring cycle. Crash-proof: a failing sensor or a locked DB
        skips this tick rather than killing the daemon."""
        for sensor in self.sensors:
            try:
                events = sensor.poll()
            except Exception as exc:  # e.g. sqlite locked, disk full
                print(f"sensor {getattr(sensor, 'name', '?')} failed this tick: {exc}",
                      file=sys.stderr, flush=True)
                continue
            for event in events:
                try:
                    self.store.record_event(event)
                    if event.severity != Severity.INFO:  # suppress initial "is UP" noise
                        self.alerter.send(event)
                except Exception as exc:
                    print(f"event handling failed: {exc}", file=sys.stderr, flush=True)
        self._maybe_write_status_page()

    def run(self) -> None:
        print(f"sentinel watching {len(self.settings.targets)} target(s), "
              f"every {self.settings.interval_seconds}s. Ctrl+C to stop.", flush=True)
        last_prune = 0.0
        try:
            while True:
                self.tick()
                now = time.time()
                if now - last_prune > 3600:  # prune old history at most hourly
                    try:
                        self.store.prune(now - self.settings.retention_days * 86400)
                    except Exception as exc:
                        print(f"prune failed: {exc}", file=sys.stderr, flush=True)
                    last_prune = now
                time.sleep(self.settings.interval_seconds)
        except KeyboardInterrupt:
            print("stopped.", flush=True)

    # -- one-shot mode (cron / CI) ------------------------------------------
    def check_once(self) -> int:
        """A stateless, point-in-time probe for cron/CI.

        Deliberately does NOT debounce: a fresh process has no history, so it
        reports what is true *right now* and returns an exit code from it. The
        stateful concerns — flap debounce, incident tracking and cert-expiry
        de-duplication — belong to the long-running ``run`` daemon, which keeps
        the per-target state machine alive across cycles.
        """
        now = time.time()
        statuses: list[str] = []
        for t in self.settings.targets:
            result = run_check(t)
            status = Status.UP if result.ok else Status.DOWN
            if (result.ok and t.degraded_ms and result.latency_ms
                    and result.latency_ms > t.degraded_ms):
                status = Status.DEGRADED
            self.store.record_sample(t.name, result.ok, status.value, result.latency_ms, now)
            self.uptime.last[t.name] = {
                "status": status.value, "latency_ms": result.latency_ms,
                "detail": result.detail, "ts": now,
            }
            statuses.append(status.value)
        self._maybe_write_status_page()
        return _EXIT_CODE[self._overall(statuses)]

    # -- snapshot / reporting ------------------------------------------------
    def snapshot(self) -> dict:
        now = time.time()
        since = now - 24 * 3600
        targets = []
        for t in self.settings.targets:
            last = self.uptime.last.get(t.name, {})
            targets.append({
                "name": t.name,
                "type": t.type,
                "target": t.target,
                "status": last.get("status", "unknown"),
                "latency_ms": last.get("latency_ms"),
                "uptime": self.store.uptime(t.name, since),
                "detail": last.get("detail", ""),
                "last_ts": last.get("ts"),
            })
        return {
            "generated_at": now,
            "overall": self._overall([x["status"] for x in targets]),
            "targets": targets,
            "incidents": [dict(r) for r in self.store.open_incidents()],
            "events": [dict(r) for r in self.store.recent_events(15)],
        }

    @staticmethod
    def _overall(statuses: list[str]) -> str:
        for level in _STATUS_ORDER:
            if level in statuses:
                return level
        return "up"

    def _maybe_write_status_page(self) -> None:
        if not self.settings.status_page:
            return
        try:
            Path(self.settings.status_page).write_text(
                render_html(self.snapshot()), encoding="utf-8"
            )
        except OSError as exc:  # locked file, missing dir, full disk — never fatal
            print(f"could not write status page: {exc}", file=sys.stderr, flush=True)
