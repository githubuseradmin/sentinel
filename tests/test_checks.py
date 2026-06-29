"""Tests for the pure (no-network) helpers in checks.py."""

import unittest

from sentinel.checks import cert_days_left, parse_host_port


class TestParseHostPort(unittest.TestCase):
    def test_host_port(self):
        self.assertEqual(parse_host_port("example.com:443"), ("example.com", 443))

    def test_bare_host_with_default_port(self):
        self.assertEqual(
            parse_host_port("example.com", default_port=80), ("example.com", 80)
        )

    def test_bare_host_without_default_port(self):
        self.assertEqual(parse_host_port("example.com"), ("example.com", None))

    def test_url_with_scheme_and_path(self):
        self.assertEqual(
            parse_host_port("https://example.com:443/some/path"),
            ("example.com", 443),
        )

    def test_url_with_scheme_no_port_uses_default(self):
        self.assertEqual(
            parse_host_port("https://example.com/path", default_port=443),
            ("example.com", 443),
        )

    def test_bracketed_ipv6_with_port(self):
        self.assertEqual(parse_host_port("[::1]:443"), ("::1", 443))

    def test_bracketed_ipv6_without_port_uses_default(self):
        self.assertEqual(
            parse_host_port("[::1]", default_port=443), ("::1", 443)
        )

    def test_empty_port_falls_back_to_default(self):
        # "host:" -> empty port string -> default_port
        self.assertEqual(
            parse_host_port("example.com:", default_port=22), ("example.com", 22)
        )

    def test_whitespace_is_stripped(self):
        self.assertEqual(parse_host_port("  example.com:8080  "), ("example.com", 8080))


class TestCertDaysLeft(unittest.TestCase):
    # ssl.cert_time_to_seconds-compatible "notAfter" strings.
    FUTURE = "Jun  1 12:00:00 2035 GMT"
    PAST = "Jun  1 12:00:00 2000 GMT"

    def _now(self, year):
        # A fixed reference time mid-2025-ish derived from a known cert string.
        import ssl

        return ssl.cert_time_to_seconds(f"Jun  1 12:00:00 {year} GMT")

    def test_positive_when_future(self):
        now = self._now(2025)
        days = cert_days_left(self.FUTURE, now=now)
        self.assertGreater(days, 0)

    def test_negative_when_expired(self):
        now = self._now(2025)
        days = cert_days_left(self.PAST, now=now)
        self.assertLess(days, 0)

    def test_exact_ten_days(self):
        import ssl

        not_after = "Jun 11 12:00:00 2025 GMT"
        now = ssl.cert_time_to_seconds("Jun  1 12:00:00 2025 GMT")
        self.assertEqual(cert_days_left(not_after, now=now), 10)

    def test_same_instant_is_zero(self):
        import ssl

        s = "Jun  1 12:00:00 2030 GMT"
        now = ssl.cert_time_to_seconds(s)
        self.assertEqual(cert_days_left(s, now=now), 0)


if __name__ == "__main__":
    unittest.main()
