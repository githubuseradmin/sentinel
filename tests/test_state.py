"""Exhaustive tests for the StateTracker debounce / hysteresis state machine."""

import unittest

from sentinel.models import CheckResult, Status
from sentinel.state import StateTracker


def ok(latency_ms=None):
    return CheckResult(ok=True, latency_ms=latency_ms, detail="ok")


def fail(detail="boom"):
    return CheckResult(ok=False, latency_ms=None, detail=detail)


class TestStateTrackerBasics(unittest.TestCase):
    def test_initial_status_is_unknown(self):
        tr = StateTracker()
        self.assertEqual(tr.status, Status.UNKNOWN)

    def test_unknown_to_up_on_first_ok(self):
        tr = StateTracker(fail_threshold=2, recover_threshold=2)
        transition = tr.update(ok())
        self.assertEqual(transition, Status.UP)
        self.assertEqual(tr.status, Status.UP)

    def test_thresholds_floored_to_one(self):
        # max(1, ...) means 0/negative thresholds become 1.
        tr = StateTracker(fail_threshold=0, recover_threshold=-5)
        self.assertEqual(tr.fail_threshold, 1)
        self.assertEqual(tr.recover_threshold, 1)


class TestFailureDebounce(unittest.TestCase):
    def test_single_failure_does_not_go_down(self):
        tr = StateTracker(fail_threshold=2)
        tr.update(ok())  # UNKNOWN -> UP
        transition = tr.update(fail())
        self.assertIsNone(transition)
        self.assertEqual(tr.status, Status.UP)

    def test_threshold_consecutive_failures_go_down(self):
        tr = StateTracker(fail_threshold=2)
        tr.update(ok())  # UP
        self.assertIsNone(tr.update(fail()))  # 1st fail, still UP
        transition = tr.update(fail())  # 2nd fail -> DOWN
        self.assertEqual(transition, Status.DOWN)
        self.assertEqual(tr.status, Status.DOWN)

    def test_further_failures_after_down_return_none(self):
        tr = StateTracker(fail_threshold=2)
        tr.update(ok())
        tr.update(fail())
        tr.update(fail())  # now DOWN
        # Already DOWN; additional failures are not transitions.
        self.assertIsNone(tr.update(fail()))
        self.assertIsNone(tr.update(fail()))
        self.assertEqual(tr.status, Status.DOWN)

    def test_intermittent_success_resets_fail_counter(self):
        # A single ok between failures must reset the consecutive-fail count.
        tr = StateTracker(fail_threshold=3)
        tr.update(ok())  # UP
        tr.update(fail())  # 1
        tr.update(fail())  # 2
        tr.update(ok())  # reset, back to UP (no transition recorded value None? it's still UP)
        self.assertEqual(tr.status, Status.UP)
        tr.update(fail())  # 1 again
        tr.update(fail())  # 2 again
        self.assertIsNone(tr.update(ok()))  # would-be 3 but ok resets; still UP
        self.assertEqual(tr.status, Status.UP)

    def test_higher_fail_threshold(self):
        tr = StateTracker(fail_threshold=4)
        tr.update(ok())
        self.assertIsNone(tr.update(fail()))  # 1
        self.assertIsNone(tr.update(fail()))  # 2
        self.assertIsNone(tr.update(fail()))  # 3
        self.assertEqual(tr.update(fail()), Status.DOWN)  # 4


class TestUnknownToDown(unittest.TestCase):
    def test_unknown_to_down_after_threshold(self):
        # Starting from UNKNOWN (never had an ok), threshold failures -> DOWN.
        tr = StateTracker(fail_threshold=2)
        self.assertIsNone(tr.update(fail()))  # 1st fail, still UNKNOWN
        self.assertEqual(tr.status, Status.UNKNOWN)
        transition = tr.update(fail())  # 2nd fail -> DOWN
        self.assertEqual(transition, Status.DOWN)
        self.assertEqual(tr.status, Status.DOWN)

    def test_unknown_single_fail_stays_unknown(self):
        tr = StateTracker(fail_threshold=3)
        self.assertIsNone(tr.update(fail()))
        self.assertEqual(tr.status, Status.UNKNOWN)


class TestRecovery(unittest.TestCase):
    def _drive_down(self, tr):
        tr.update(ok())
        tr.update(fail())
        tr.update(fail())
        assert tr.status == Status.DOWN

    def test_recovery_needs_recover_threshold_successes(self):
        tr = StateTracker(fail_threshold=2, recover_threshold=2)
        self._drive_down(tr)
        # First ok while DOWN is not enough.
        self.assertIsNone(tr.update(ok()))
        self.assertEqual(tr.status, Status.DOWN)
        # Second consecutive ok clears the outage.
        transition = tr.update(ok())
        self.assertEqual(transition, Status.UP)
        self.assertEqual(tr.status, Status.UP)

    def test_recovery_interrupted_by_failure_resets(self):
        tr = StateTracker(fail_threshold=2, recover_threshold=3)
        self._drive_down(tr)
        self.assertIsNone(tr.update(ok()))  # 1
        self.assertIsNone(tr.update(ok()))  # 2
        # A failure interrupts; counter resets, still DOWN.
        self.assertIsNone(tr.update(fail()))
        self.assertEqual(tr.status, Status.DOWN)
        self.assertIsNone(tr.update(ok()))  # 1
        self.assertIsNone(tr.update(ok()))  # 2
        self.assertEqual(tr.update(ok()), Status.UP)  # 3 -> recovered

    def test_recover_threshold_one_recovers_immediately(self):
        tr = StateTracker(fail_threshold=2, recover_threshold=1)
        self._drive_down(tr)
        self.assertEqual(tr.update(ok()), Status.UP)

    def test_recovery_to_degraded_when_slow(self):
        # On recovery, a slow-but-ok probe should land in DEGRADED, not UP.
        tr = StateTracker(fail_threshold=2, recover_threshold=1, degraded_ms=100.0)
        self._drive_down(tr)
        transition = tr.update(ok(latency_ms=500.0))
        self.assertEqual(transition, Status.DEGRADED)
        self.assertEqual(tr.status, Status.DEGRADED)


class TestDegradedHysteresis(unittest.TestCase):
    def test_up_to_degraded_on_high_latency(self):
        tr = StateTracker(degraded_ms=100.0)
        self.assertEqual(tr.update(ok(latency_ms=10.0)), Status.UP)
        transition = tr.update(ok(latency_ms=250.0))
        self.assertEqual(transition, Status.DEGRADED)
        self.assertEqual(tr.status, Status.DEGRADED)

    def test_degraded_to_up_on_low_latency(self):
        tr = StateTracker(degraded_ms=100.0)
        tr.update(ok(latency_ms=250.0))  # UNKNOWN -> DEGRADED
        self.assertEqual(tr.status, Status.DEGRADED)
        transition = tr.update(ok(latency_ms=10.0))
        self.assertEqual(transition, Status.UP)
        self.assertEqual(tr.status, Status.UP)

    def test_unknown_to_degraded_directly(self):
        tr = StateTracker(degraded_ms=100.0)
        transition = tr.update(ok(latency_ms=999.0))
        self.assertEqual(transition, Status.DEGRADED)

    def test_no_degraded_when_threshold_none(self):
        tr = StateTracker(degraded_ms=None)
        transition = tr.update(ok(latency_ms=10_000.0))
        self.assertEqual(transition, Status.UP)

    def test_latency_exactly_at_threshold_is_up(self):
        # The check is strictly greater-than, so == threshold stays UP.
        tr = StateTracker(degraded_ms=100.0)
        transition = tr.update(ok(latency_ms=100.0))
        self.assertEqual(transition, Status.UP)

    def test_none_latency_never_degrades(self):
        tr = StateTracker(degraded_ms=100.0)
        transition = tr.update(ok(latency_ms=None))
        self.assertEqual(transition, Status.UP)

    def test_staying_degraded_returns_none(self):
        tr = StateTracker(degraded_ms=100.0)
        tr.update(ok(latency_ms=250.0))  # -> DEGRADED
        self.assertIsNone(tr.update(ok(latency_ms=300.0)))  # still DEGRADED
        self.assertEqual(tr.status, Status.DEGRADED)

    def test_staying_up_returns_none(self):
        tr = StateTracker(degraded_ms=100.0)
        tr.update(ok(latency_ms=10.0))  # -> UP
        self.assertIsNone(tr.update(ok(latency_ms=20.0)))  # still UP


if __name__ == "__main__":
    unittest.main()
