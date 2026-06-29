"""Uptime / TLS sensor: probes each target, debounces, and emits events.

This is the "watchtower" capability expressed as a sentinel sensor. Each tick it
runs every target's check, feeds the result into that target's state machine, and
emits an Event only on a *state change* (so alerts fire on transitions, not every
poll) plus a one-shot certificate-expiry warning.
"""

from __future__ import annotations

import time

from ..checks import run_check
from ..models import Event, Severity, Status
from ..state import StateTracker
from . import Sensor


class UptimeSensor(Sensor):
    name = "uptime"

    def __init__(self, targets, store) -> None:
        self.targets = targets
        self.store = store
        self.trackers = {
            t.name: StateTracker(t.fail_threshold, t.recover_threshold, t.degraded_ms)
            for t in targets
        }
        # Targets currently in a cert-expiry warning state (dedupe the warning).
        self._cert_warned: set[str] = set()
        # Latest result per target, for the status snapshot.
        self.last: dict[str, dict] = {}

    def poll(self) -> list[Event]:
        events: list[Event] = []
        now = time.time()
        for t in self.targets:
            result = run_check(t)
            tracker = self.trackers[t.name]
            prev = tracker.status
            transition = tracker.update(result)

            self.store.record_sample(t.name, result.ok, tracker.status.value, result.latency_ms, now)
            self.last[t.name] = {
                "status": tracker.status.value,
                "latency_ms": result.latency_ms,
                "detail": result.detail,
                "ts": now,
            }

            if transition is not None:
                events.append(self._state_event(t, prev, transition, result, now))
                if transition == Status.DOWN:
                    self.store.open_incident(t.name, "critical", result.detail, now)
                elif prev == Status.DOWN:
                    self.store.close_incident(t.name, now)

            events.extend(self._cert_events(t, result, now))
        return events

    def _state_event(self, t, prev: Status, new: Status, result, now: float) -> Event:
        if new == Status.DOWN:
            return Event("state_change", Severity.CRITICAL, t.name,
                         f"{t.name} is DOWN", result.detail, now)
        if new == Status.DEGRADED:
            lat = f" ({result.latency_ms:.0f} ms)" if result.latency_ms is not None else ""
            return Event("state_change", Severity.WARNING, t.name,
                         f"{t.name} is DEGRADED{lat}", result.detail, now)
        # new == UP
        if prev == Status.DOWN:
            return Event("state_change", Severity.RECOVERY, t.name,
                         f"{t.name} recovered (UP)", result.detail, now)
        return Event("state_change", Severity.INFO, t.name,
                     f"{t.name} is UP", result.detail, now)

    def _cert_events(self, t, result, now: float) -> list[Event]:
        days = result.metrics.get("cert_days_left")
        if days is None:
            return []
        if 0 <= days <= t.cert_warn_days:
            if t.name in self._cert_warned:
                return []
            self._cert_warned.add(t.name)
            return [Event("cert_expiring", Severity.WARNING, t.name,
                          f"{t.name}: TLS certificate expires in {days} days",
                          result.detail, now)]
        # Healthy (days > warn) OR already expired (days < 0): clear the warned
        # flag so a renewed-then-expiring cert re-warns. An expired cert is also
        # surfaced as a DOWN check by check_tls.
        self._cert_warned.discard(t.name)
        return []
