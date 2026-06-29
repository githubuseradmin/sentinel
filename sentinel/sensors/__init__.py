"""Sensor plugin layer.

A *sensor* observes the world and emits :class:`~sentinel.models.Event` objects
into the shared core (store + alerter + reporter). The shipped poll-based sensor
is ``uptime`` (availability / TLS). The same seam is meant to host a listener-
based intrusion sensor (an SSH honeypot) next: it would run its own socket loop
and emit ``intrusion`` events through the very same core.
"""

from __future__ import annotations

from ..models import Event


class Sensor:
    """Base class. Poll-based sensors return new events from ``poll`` each tick."""

    name = "sensor"

    def poll(self) -> list[Event]:  # pragma: no cover - overridden
        return []
