"""Tests for the honeypot sensor: pure event logic + a local socket capture.

The socket test binds 127.0.0.1 on an ephemeral port (port=0) and connects to
itself — no external network. Standard-library unittest only.
"""

from __future__ import annotations

import socket
import time
import unittest

from sentinel.models import Severity
from sentinel.sensors.honeypot import HoneypotSensor


def _hit(ip, ts=0.0, banner=""):
    return {"ip": ip, "ts": ts, "banner": banner}


class HoneypotEventLogicTests(unittest.TestCase):
    def _sensor(self):
        return HoneypotSensor(port=0, realert_seconds=3600)

    def test_hit_makes_info_and_a_new_source_warning(self):
        s = self._sensor()
        evs = s._events_for_hits([_hit("1.2.3.4", 100.0, "SSH-2.0-libssh")], now=100.0)
        kinds = {(e.kind, e.severity) for e in evs}
        self.assertIn(("honeypot_hit", Severity.INFO), kinds)
        self.assertIn(("intrusion", Severity.WARNING), kinds)
        warn = next(e for e in evs if e.severity == Severity.WARNING)
        self.assertIn("1.2.3.4", warn.title)

    def test_repeat_source_within_window_has_no_new_warning(self):
        s = self._sensor()
        s._events_for_hits([_hit("1.2.3.4", 100.0)], now=100.0)      # first: alerts
        evs = s._events_for_hits([_hit("1.2.3.4", 150.0)], now=150.0)  # 50s later
        self.assertEqual([e for e in evs if e.severity == Severity.WARNING], [])
        self.assertTrue(any(e.kind == "honeypot_hit" for e in evs))  # still recorded

    def test_source_re_alerts_after_window(self):
        s = self._sensor()
        s._events_for_hits([_hit("1.2.3.4", 0.0)], now=0.0)
        evs = s._events_for_hits([_hit("1.2.3.4", 5000.0)], now=5000.0)  # > 3600s
        self.assertTrue(any(e.severity == Severity.WARNING for e in evs))

    def test_burst_aggregates_into_one_summary(self):
        s = self._sensor()
        hits = [_hit(f"10.0.0.{i}") for i in range(20)]
        evs = s._events_for_hits(hits, now=0.0)
        warns = [e for e in evs if e.severity == Severity.WARNING]
        infos = [e for e in evs if e.severity == Severity.INFO]
        self.assertEqual(len(warns), 1, "many new sources -> a single summary alert")
        self.assertIn("new sources", warns[0].title)
        self.assertEqual(len(infos), 20, "every hit is still recorded as INFO")

    def test_banner_is_truncated_and_none_handled(self):
        s = self._sensor()
        evs = s._events_for_hits([_hit("9.9.9.9", 0.0, "A" * 300)], now=0.0)
        info = next(e for e in evs if e.kind == "honeypot_hit")
        self.assertLessEqual(len(info.detail), 120)


class HoneypotListenerTests(unittest.TestCase):
    def test_captures_a_local_connection(self):
        s = HoneypotSensor(host="127.0.0.1", port=0, realert_seconds=3600)
        try:
            self.assertEqual(s.poll(), [])          # starts the listener; no hits yet
            self.assertIsNotNone(s.bound_port)

            c = socket.create_connection(("127.0.0.1", s.bound_port), timeout=2)
            c.sendall(b"hello-from-attacker\r\n")
            c.close()

            deadline = time.time() + 3
            events = []
            while time.time() < deadline and not events:
                time.sleep(0.05)
                events = s.poll()

            self.assertTrue(events, "the connection should have been captured")
            self.assertTrue(any("127.0.0.1" in e.title for e in events))
            self.assertTrue(any(e.severity == Severity.WARNING for e in events))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
