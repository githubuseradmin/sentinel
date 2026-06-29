"""Tests for the Engine: check_once exit codes and tick() incident handling.

All network probes are replaced by monkeypatching ``run_check`` so nothing
touches the network. A temporary db_path is used and cleaned up.
"""

import os
import tempfile
import unittest
from unittest import mock

from sentinel.config import Settings
from sentinel.engine import Engine
from sentinel.models import CheckResult, Severity, Status, Target


def _target(name, **kw):
    return Target(name=name, type="http", target=f"https://{name}.example.com", **kw)


def _settings(targets, db_path):
    return Settings(
        interval_seconds=60,
        db_path=db_path,
        status_page=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        targets=targets,
    )


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="sentinel_eng_")
        os.close(fd)
        os.unlink(self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


class TestCheckOnceExitCodes(EngineTestCase):
    def test_all_ok_exits_zero(self):
        targets = [_target("a"), _target("b")]
        engine = Engine(_settings(targets, self.db_path))

        def fake(t):
            return CheckResult(ok=True, latency_ms=10.0, detail="HTTP 200")

        with mock.patch("sentinel.engine.run_check", side_effect=fake):
            self.assertEqual(engine.check_once(), 0)

    def test_slow_but_ok_with_degraded_ms_exits_one(self):
        targets = [_target("a", degraded_ms=100.0)]
        engine = Engine(_settings(targets, self.db_path))

        def fake(t):
            return CheckResult(ok=True, latency_ms=500.0, detail="slow")

        with mock.patch("sentinel.engine.run_check", side_effect=fake):
            self.assertEqual(engine.check_once(), 1)

    def test_one_failing_exits_two(self):
        targets = [_target("a"), _target("b")]
        engine = Engine(_settings(targets, self.db_path))

        def fake(t):
            if t.name == "b":
                return CheckResult(ok=False, latency_ms=None, detail="refused")
            return CheckResult(ok=True, latency_ms=10.0, detail="HTTP 200")

        with mock.patch("sentinel.engine.run_check", side_effect=fake):
            # down beats up in overall ordering -> exit 2.
            self.assertEqual(engine.check_once(), 2)

    def test_down_takes_precedence_over_degraded(self):
        targets = [_target("slow", degraded_ms=10.0), _target("dead")]
        engine = Engine(_settings(targets, self.db_path))

        def fake(t):
            if t.name == "dead":
                return CheckResult(ok=False, latency_ms=None, detail="x")
            return CheckResult(ok=True, latency_ms=999.0, detail="slow")

        with mock.patch("sentinel.engine.run_check", side_effect=fake):
            self.assertEqual(engine.check_once(), 2)

    def test_records_samples(self):
        targets = [_target("a")]
        engine = Engine(_settings(targets, self.db_path))
        with mock.patch(
            "sentinel.engine.run_check",
            side_effect=lambda t: CheckResult(ok=True, latency_ms=5.0, detail="ok"),
        ):
            engine.check_once()
        # A sample was persisted; uptime is computable -> 100%.
        self.assertEqual(engine.store.uptime("a", since_ts=0), 100.0)


class TestTickIncidentFlow(EngineTestCase):
    def test_down_transition_opens_incident_and_emits_critical(self):
        # fail_threshold=2 -> need two failing ticks to flip to DOWN.
        targets = [_target("a", fail_threshold=2)]
        engine = Engine(_settings(targets, self.db_path))

        failing = CheckResult(ok=False, latency_ms=None, detail="connection refused")

        with mock.patch(
            "sentinel.sensors.uptime.run_check", side_effect=lambda t: failing
        ):
            engine.tick()  # 1st failure: no transition yet
            self.assertEqual(engine.store.open_incidents(), [])
            engine.tick()  # 2nd failure: -> DOWN

        open_ones = engine.store.open_incidents()
        self.assertEqual(len(open_ones), 1)
        self.assertEqual(open_ones[0]["target"], "a")

        events = engine.store.recent_events()
        crit = [e for e in events if e["severity"] == Severity.CRITICAL.value]
        self.assertTrue(crit, "expected a CRITICAL state_change event")
        self.assertIn("DOWN", crit[0]["title"])

    def test_recovery_closes_incident(self):
        targets = [_target("a", fail_threshold=2, recover_threshold=2)]
        engine = Engine(_settings(targets, self.db_path))

        failing = CheckResult(ok=False, latency_ms=None, detail="refused")
        healthy = CheckResult(ok=True, latency_ms=10.0, detail="HTTP 200")

        with mock.patch(
            "sentinel.sensors.uptime.run_check", side_effect=lambda t: failing
        ):
            engine.tick()
            engine.tick()  # DOWN, incident open
        self.assertEqual(len(engine.store.open_incidents()), 1)

        with mock.patch(
            "sentinel.sensors.uptime.run_check", side_effect=lambda t: healthy
        ):
            engine.tick()  # 1 ok, still DOWN
            self.assertEqual(len(engine.store.open_incidents()), 1)
            engine.tick()  # 2 oks -> recovered, incident closed
        self.assertEqual(engine.store.open_incidents(), [])

    def test_single_failure_does_not_open_incident(self):
        targets = [_target("a", fail_threshold=3)]
        engine = Engine(_settings(targets, self.db_path))
        failing = CheckResult(ok=False, latency_ms=None, detail="refused")
        with mock.patch(
            "sentinel.sensors.uptime.run_check", side_effect=lambda t: failing
        ):
            engine.tick()
        self.assertEqual(engine.store.open_incidents(), [])

    def test_snapshot_shape_after_tick(self):
        targets = [_target("a")]
        engine = Engine(_settings(targets, self.db_path))
        healthy = CheckResult(ok=True, latency_ms=10.0, detail="HTTP 200")
        with mock.patch(
            "sentinel.sensors.uptime.run_check", side_effect=lambda t: healthy
        ):
            engine.tick()
        snap = engine.snapshot()
        self.assertEqual(snap["overall"], "up")
        self.assertEqual(len(snap["targets"]), 1)
        self.assertEqual(snap["targets"][0]["name"], "a")
        self.assertEqual(snap["targets"][0]["status"], "up")


if __name__ == "__main__":
    unittest.main()
