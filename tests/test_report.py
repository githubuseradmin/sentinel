"""Tests for the report renderers (HTML / JSON / text)."""

import json
import unittest

from sentinel.report import render_html, render_json, render_text


def _snapshot():
    return {
        "generated_at": 1_700_000_000.0,
        "overall": "degraded",
        "targets": [
            {
                "name": "web",
                "type": "http",
                "target": "https://example.com",
                "status": "up",
                "latency_ms": 42.0,
                "uptime": 99.95,
                "detail": "HTTP 200",
                "last_ts": 1_700_000_000.0,
            },
            {
                "name": "api",
                "type": "http",
                "target": "https://api.example.com",
                "status": "degraded",
                "latency_ms": 850.0,
                "uptime": 97.5,
                "detail": "slow",
                "last_ts": 1_700_000_000.0,
            },
        ],
        "incidents": [
            {"target": "db", "detail": "connection refused", "started_ts": 1_699_000_000.0},
        ],
        "events": [
            {"severity": "warning", "title": "api is DEGRADED", "ts": 1_700_000_000.0},
        ],
    }


class TestRenderHtml(unittest.TestCase):
    def test_contains_target_names(self):
        out = render_html(_snapshot())
        self.assertIn("web", out)
        self.assertIn("api", out)

    def test_contains_overall_status(self):
        out = render_html(_snapshot())
        # Overall is rendered uppercased in the badge.
        self.assertIn("DEGRADED", out)

    def test_returns_string(self):
        self.assertIsInstance(render_html(_snapshot()), str)

    def test_html_escapes_dangerous_field(self):
        snap = _snapshot()
        snap["targets"][0]["name"] = "<script>alert(1)</script>"
        snap["targets"][0]["detail"] = "<b>raw</b>"
        out = render_html(snap)
        # Raw injected tags must NOT appear; their escaped form must.
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<b>raw</b>", out)
        self.assertIn("&lt;b&gt;raw&lt;/b&gt;", out)

    def test_escapes_incident_and_event_fields(self):
        snap = _snapshot()
        snap["incidents"][0]["detail"] = "<img src=x>"
        snap["events"][0]["title"] = "<svg onload=1>"
        out = render_html(snap)
        self.assertNotIn("<img src=x>", out)
        self.assertNotIn("<svg onload=1>", out)

    def test_no_targets_renders_placeholder(self):
        snap = _snapshot()
        snap["targets"] = []
        out = render_html(snap)
        self.assertIn("No targets.", out)


class TestRenderJson(unittest.TestCase):
    def test_round_trips(self):
        snap = _snapshot()
        out = render_json(snap)
        parsed = json.loads(out)
        self.assertEqual(parsed["overall"], "degraded")
        self.assertEqual(len(parsed["targets"]), 2)
        self.assertEqual(parsed["targets"][0]["name"], "web")

    def test_is_valid_json_string(self):
        out = render_json(_snapshot())
        self.assertIsInstance(out, str)
        json.loads(out)  # would raise if invalid


class TestRenderText(unittest.TestCase):
    def test_contains_overall_and_targets(self):
        out = render_text(_snapshot())
        self.assertIn("DEGRADED", out)
        self.assertIn("web", out)
        self.assertIn("api", out)

    def test_contains_statuses(self):
        out = render_text(_snapshot())
        self.assertIn("up", out)
        self.assertIn("degraded", out)

    def test_lists_open_incidents(self):
        out = render_text(_snapshot())
        self.assertIn("Open incidents:", out)
        self.assertIn("db", out)

    def test_returns_string(self):
        self.assertIsInstance(render_text(_snapshot()), str)


if __name__ == "__main__":
    unittest.main()
