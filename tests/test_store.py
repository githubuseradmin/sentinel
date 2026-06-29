"""Tests for the SQLite Store: uptime %, incidents, events."""

import os
import tempfile
import unittest

from sentinel.models import Event, Severity
from sentinel.store import Store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="sentinel_test_")
        os.close(fd)
        os.unlink(self.db_path)  # let Store create it fresh
        self.store = Store(self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


class TestUptime(StoreTestCase):
    def test_no_samples_returns_none(self):
        self.assertIsNone(self.store.uptime("web", since_ts=0))

    def test_three_of_four_is_75_percent(self):
        for ok in (True, True, True, False):
            self.store.record_sample("web", ok, "up", 12.0, ts=100.0)
        self.assertEqual(self.store.uptime("web", since_ts=0), 75.0)

    def test_all_ok_is_100(self):
        for _ in range(5):
            self.store.record_sample("web", True, "up", 5.0, ts=100.0)
        self.assertEqual(self.store.uptime("web", since_ts=0), 100.0)

    def test_all_fail_is_zero(self):
        for _ in range(3):
            self.store.record_sample("web", False, "down", None, ts=100.0)
        self.assertEqual(self.store.uptime("web", since_ts=0), 0.0)

    def test_since_ts_filters_old_samples(self):
        self.store.record_sample("web", False, "down", None, ts=10.0)  # old, excluded
        self.store.record_sample("web", True, "up", 5.0, ts=200.0)
        self.store.record_sample("web", True, "up", 5.0, ts=201.0)
        # Only the two recent (both ok) samples count -> 100%.
        self.assertEqual(self.store.uptime("web", since_ts=100.0), 100.0)

    def test_uptime_is_per_target(self):
        self.store.record_sample("a", True, "up", 1.0, ts=100.0)
        self.store.record_sample("b", False, "down", None, ts=100.0)
        self.assertEqual(self.store.uptime("a", since_ts=0), 100.0)
        self.assertEqual(self.store.uptime("b", since_ts=0), 0.0)


class TestIncidents(StoreTestCase):
    def test_open_incident_is_idempotent(self):
        self.store.open_incident("web", "critical", "down", ts=100.0)
        self.store.open_incident("web", "critical", "down again", ts=200.0)
        open_ones = self.store.open_incidents()
        self.assertEqual(len(open_ones), 1)
        self.assertEqual(open_ones[0]["started_ts"], 100.0)
        self.assertEqual(open_ones[0]["detail"], "down")  # first one preserved

    def test_close_incident_sets_ended_ts(self):
        self.store.open_incident("web", "critical", "down", ts=100.0)
        self.store.close_incident("web", ts=300.0)
        self.assertEqual(self.store.open_incidents(), [])

    def test_reopen_after_close(self):
        self.store.open_incident("web", "critical", "down", ts=100.0)
        self.store.close_incident("web", ts=200.0)
        # No longer ongoing, so a new one can open.
        self.store.open_incident("web", "critical", "down2", ts=300.0)
        open_ones = self.store.open_incidents()
        self.assertEqual(len(open_ones), 1)
        self.assertEqual(open_ones[0]["started_ts"], 300.0)

    def test_open_incidents_lists_only_ongoing(self):
        self.store.open_incident("a", "critical", "x", ts=100.0)
        self.store.open_incident("b", "critical", "y", ts=110.0)
        self.store.close_incident("a", ts=200.0)
        open_ones = self.store.open_incidents()
        self.assertEqual([r["target"] for r in open_ones], ["b"])

    def test_open_incidents_ordered_by_started_ts(self):
        self.store.open_incident("late", "critical", "x", ts=300.0)
        self.store.open_incident("early", "critical", "y", ts=100.0)
        open_ones = self.store.open_incidents()
        self.assertEqual([r["target"] for r in open_ones], ["early", "late"])

    def test_close_nonexistent_is_noop(self):
        # Should not raise even with nothing open.
        self.store.close_incident("ghost", ts=100.0)
        self.assertEqual(self.store.open_incidents(), [])


class TestEvents(StoreTestCase):
    def _event(self, title, ts, severity=Severity.WARNING):
        return Event(
            kind="state_change",
            severity=severity,
            source="web",
            title=title,
            detail="d",
            ts=ts,
        )

    def test_record_and_read_event(self):
        self.store.record_event(self._event("hello", ts=100.0))
        rows = self.store.recent_events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "hello")
        self.assertEqual(rows[0]["severity"], "warning")
        self.assertEqual(rows[0]["source"], "web")

    def test_recent_events_newest_first(self):
        self.store.record_event(self._event("oldest", ts=100.0))
        self.store.record_event(self._event("middle", ts=200.0))
        self.store.record_event(self._event("newest", ts=300.0))
        rows = self.store.recent_events()
        self.assertEqual(
            [r["title"] for r in rows], ["newest", "middle", "oldest"]
        )

    def test_recent_events_respects_limit(self):
        # Use ts>=1: record_event treats a falsy ts (0.0) as "unset" and
        # substitutes time.time(), which would scramble this ordering.
        for i in range(10):
            self.store.record_event(self._event(f"e{i}", ts=float(i + 1)))
        rows = self.store.recent_events(limit=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["title"] for r in rows], ["e9", "e8", "e7"])

    def test_severity_stored_as_value(self):
        self.store.record_event(self._event("crit", ts=1.0, severity=Severity.CRITICAL))
        rows = self.store.recent_events()
        self.assertEqual(rows[0]["severity"], "critical")


if __name__ == "__main__":
    unittest.main()
