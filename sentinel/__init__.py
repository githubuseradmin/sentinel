"""sentinel — a small, self-hosted uptime / TLS monitor with Telegram alerts.

Plugin-based by design: an event-driven core (store + alerter + reporter) is fed
by *sensors*. The shipped sensor watches availability (HTTP / TCP / TLS / DNS);
the same core is built to host an intrusion sensor (SSH honeypot) next.
"""

__version__ = "1.0.0"
