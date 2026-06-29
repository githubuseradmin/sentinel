"""Shared data types: statuses, severities, targets, check results and events.

The whole system is event-driven: *sensors* observe the world and emit ``Event``
objects; the core stores them, alerts on them, and renders them. Keeping these
types dependency-free (stdlib dataclasses/enums) makes the logic easy to test.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class Status(str, enum.Enum):
    """Health of a monitored target. ``str`` mix-in serialises cleanly."""

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"

    @property
    def emoji(self) -> str:
        return {"up": "🟢", "degraded": "🟡", "down": "🔴", "unknown": "⚪"}[self.value]


class Severity(str, enum.Enum):
    """Importance of an event (drives alert formatting and exit codes)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    RECOVERY = "recovery"

    @property
    def emoji(self) -> str:
        return {
            "info": "ℹ️",
            "warning": "⚠️",
            "critical": "🔴",
            "recovery": "✅",
        }[self.value]


@dataclass(frozen=True)
class Target:
    """A single thing to monitor, built from the JSON config.

    ``type`` selects the check; the remaining fields are per-type options with
    sensible defaults so a minimal config (``name``/``type``/``target``) works.
    """

    name: str
    type: str           # http | tcp | tls | dns
    target: str         # URL, "host:port", or hostname depending on ``type``
    timeout: float = 10.0
    # http: expected status code and an optional substring that must appear.
    expect_status: Optional[int] = None
    expect_text: Optional[str] = None
    # Latency over this many ms marks the target DEGRADED (None disables it).
    degraded_ms: Optional[float] = None
    # tls/http: warn when the certificate expires within this many days.
    cert_warn_days: int = 14
    # dns: optional expected resolved address (A/AAAA).
    expect_ip: Optional[str] = None
    # State-machine debounce: how many consecutive results flip the state.
    fail_threshold: int = 2
    recover_threshold: int = 2


@dataclass
class CheckResult:
    """Outcome of one probe of a target."""

    ok: bool
    latency_ms: Optional[float] = None
    detail: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class Event:
    """Something worth recording / alerting on, emitted by a sensor."""

    kind: str           # e.g. "state_change", "cert_expiring", "intrusion"
    severity: Severity
    source: str         # target or sensor name
    title: str
    detail: str = ""
    ts: float = 0.0     # unix epoch seconds (set by the sensor)
