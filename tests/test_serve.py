"""Tests for the pure HTTP routing function in :mod:`sentinel.serve`.

Only the socket-free ``route`` function is exercised here — no real server is
bound, so these tests are fast and deterministic.
"""

import json
import unittest

from sentinel.serve import route


def _snapshot():
    return {
        "generated_at": 1_700_000_000.0,
        "overall": "up",
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
        ],
        "incidents": [],
        "events": [],
    }


class TestRoute(unittest.TestCase):
    def test_root_serves_html_page(self):
        status, content_type, body = route(_snapshot(), "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn("web", body)  # target name rendered into the page

    def test_status_json_serves_json_snapshot(self):
        status, content_type, body = route(_snapshot(), "/status.json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        parsed = json.loads(body)  # would raise if not valid JSON
        self.assertEqual(parsed["overall"], "up")
        self.assertEqual(parsed["targets"][0]["name"], "web")

    def test_health_alias_serves_json(self):
        status, content_type, body = route(_snapshot(), "/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        json.loads(body)

    def test_json_path_ignores_query_string(self):
        status, content_type, _ = route(_snapshot(), "/status.json?pretty=1")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)

    def test_unknown_path_is_404(self):
        status, content_type, body = route(_snapshot(), "/nope")
        self.assertEqual(status, 404)
        self.assertIn("text/plain", content_type)
        self.assertIn("404", body)

    def test_favicon_is_404_not_html(self):
        status, _, _ = route(_snapshot(), "/favicon.ico")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
