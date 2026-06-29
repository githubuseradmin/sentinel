"""Per-target health state machine with flap debounce.

A single failed probe should not page anyone, and a single success should not
declare an outage over. ``StateTracker`` requires N consecutive failures to go
DOWN and M consecutive successes to recover — classic hysteresis. It is pure
(no I/O), so the transition logic is exhaustively unit-testable.

    UNKNOWN ──ok──────────────> UP/DEGRADED
            ──fail × N────────> DOWN
    UP/DEGRADED ──fail × N────> DOWN
    DOWN ──ok × M────────────> UP/DEGRADED
    UP <──latency──> DEGRADED            (soft, immediate)
"""

from __future__ import annotations

from typing import Optional

from .models import CheckResult, Status


class StateTracker:
    def __init__(
        self,
        fail_threshold: int = 2,
        recover_threshold: int = 2,
        degraded_ms: Optional[float] = None,
    ) -> None:
        self.status = Status.UNKNOWN
        self.fail_threshold = max(1, fail_threshold)
        self.recover_threshold = max(1, recover_threshold)
        self.degraded_ms = degraded_ms
        self._consec_fail = 0
        self._consec_ok = 0

    def update(self, result: CheckResult) -> Optional[Status]:
        """Feed one probe result; return the NEW status iff it changed, else None."""
        if result.ok:
            self._consec_ok += 1
            self._consec_fail = 0
        else:
            self._consec_fail += 1
            self._consec_ok = 0

        new = self.status

        if not result.ok:
            # Only fall to DOWN once enough consecutive failures have piled up.
            if self._consec_fail >= self.fail_threshold:
                new = Status.DOWN
        else:
            desired = self._healthy_status(result)
            if self.status == Status.DOWN:
                # Require sustained success before clearing an outage.
                if self._consec_ok >= self.recover_threshold:
                    new = desired
            else:
                # From UNKNOWN/UP/DEGRADED a healthy probe applies immediately.
                new = desired

        if new != self.status:
            self.status = new
            return new
        return None

    def _healthy_status(self, result: CheckResult) -> Status:
        """UP, or DEGRADED when latency crosses the configured threshold."""
        if (
            self.degraded_ms is not None
            and result.latency_ms is not None
            and result.latency_ms > self.degraded_ms
        ):
            return Status.DEGRADED
        return Status.UP
