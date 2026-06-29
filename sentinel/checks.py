"""Probes for each target type (HTTP / TCP / TLS / DNS), standard library only.

Each probe returns a :class:`CheckResult` (never raises) so the engine can treat
every outcome uniformly. The genuinely *pure* helpers — ``parse_host_port`` and
``cert_days_left`` — are split out so they can be unit-tested without a network.
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Optional

from .models import CheckResult, Target

_UA = "sentinel/1.0 (+https://github.com/githubuseradmin/sentinel)"


# ---------------------------------------------------------------------------
# Pure helpers (no network) — unit-tested
# ---------------------------------------------------------------------------
def parse_host_port(value: str, default_port: Optional[int] = None) -> tuple[str, Optional[int]]:
    """Split ``"host:port"`` / a URL / a bare host into ``(host, port)``.

    Tolerates a scheme and path (``https://h:443/x`` -> ``("h", 443)``) and
    bracketed IPv6 (``[::1]:443`` -> ``("::1", 443)``).
    """
    s = value.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    if s.startswith("[") and "]" in s:                       # [IPv6](:port)?
        host, _, rest = s[1:].partition("]")
        rest = rest.lstrip(":")
        return host, int(rest) if rest else default_port
    if s.count(":") == 1:                                    # host:port
        host, _, port = s.partition(":")
        return host, int(port) if port else default_port
    return s, default_port


def cert_days_left(not_after: str, now: Optional[float] = None) -> int:
    """Whole days until a certificate's ``notAfter`` (negative if expired)."""
    now = time.time() if now is None else now
    expiry = ssl.cert_time_to_seconds(not_after)
    return int((expiry - now) // 86400)


def _readable_name(rdn) -> str:
    """Flatten an SSL subject/issuer RDN tuple into ``O=... CN=...``."""
    if not rdn:
        return ""
    parts = []
    for entry in rdn:
        for key, val in entry:
            if key in ("organizationName", "commonName"):
                parts.append(f"{'CN' if key == 'commonName' else 'O'}={val}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Network probes
# ---------------------------------------------------------------------------
def check_http(t: Target) -> CheckResult:
    start = time.perf_counter()
    req = urllib.request.Request(t.target, headers={"User-Agent": _UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=t.timeout) as resp:
            status = resp.status
            body = resp.read(65536).decode("utf-8", "replace")
        latency = (time.perf_counter() - start) * 1000
        ok, detail = True, f"HTTP {status}"
        if t.expect_status is not None and status != t.expect_status:
            ok, detail = False, f"HTTP {status} (expected {t.expect_status})"
        elif t.expect_text and t.expect_text not in body:
            ok, detail = False, f"HTTP {status}, expected text not found"
        return CheckResult(ok, latency, detail, {"status_code": status})
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - start) * 1000
        ok = t.expect_status == exc.code
        return CheckResult(ok, latency, f"HTTP {exc.code}", {"status_code": exc.code})
    except Exception as exc:  # DNS, refused, timeout, TLS, …
        return CheckResult(False, None, f"{type(exc).__name__}: {exc}")


def check_tcp(t: Target) -> CheckResult:
    start = time.perf_counter()
    try:
        host, port = parse_host_port(t.target)  # may raise on a non-numeric port
        if port is None:
            return CheckResult(False, None, "tcp target must be host:port")
        with socket.create_connection((host, port), timeout=t.timeout):
            latency = (time.perf_counter() - start) * 1000
        return CheckResult(True, latency, f"connected {host}:{port}", {"port": port})
    except Exception as exc:
        return CheckResult(False, None, f"{type(exc).__name__}: {exc}")


def check_tls(t: Target) -> CheckResult:
    start = time.perf_counter()
    try:
        host, port = parse_host_port(t.target, default_port=443)
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=t.timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        latency = (time.perf_counter() - start) * 1000
        not_after = cert.get("notAfter") if cert else None
        days = cert_days_left(not_after) if not_after else None
        metrics = {"cert_days_left": days, "issuer": _readable_name(cert.get("issuer"))}
        if days is not None and days < 0:
            return CheckResult(False, latency, f"certificate expired {abs(days)}d ago", metrics)
        detail = f"valid, {days}d left" if days is not None else "valid"
        return CheckResult(True, latency, detail, metrics)
    except ssl.SSLCertVerificationError as exc:
        return CheckResult(False, None, f"cert verification failed: {exc.verify_message}",
                           {"cert_days_left": None})
    except Exception as exc:
        return CheckResult(False, None, f"{type(exc).__name__}: {exc}")


def check_dns(t: Target) -> CheckResult:
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(t.target, None)
        latency = (time.perf_counter() - start) * 1000
        ips = sorted({info[4][0] for info in infos})
        if t.expect_ip and t.expect_ip not in ips:
            return CheckResult(False, latency,
                               f"resolved {', '.join(ips)}, expected {t.expect_ip}",
                               {"ips": ips})
        return CheckResult(True, latency, ", ".join(ips[:5]), {"ips": ips})
    except Exception as exc:
        return CheckResult(False, None, f"{type(exc).__name__}: {exc}")


_PROBES = {"http": check_http, "tcp": check_tcp, "tls": check_tls, "dns": check_dns}


def run_check(t: Target) -> CheckResult:
    """Dispatch to the probe for ``t.type``."""
    probe = _PROBES.get(t.type)
    if probe is None:
        return CheckResult(False, None, f"unknown check type {t.type!r}")
    return probe(t)
