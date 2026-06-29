"""Alert delivery: console and Telegram (Bot HTTP API via urllib).

Pluggable and fail-safe: a delivery failure (no token, network error, Telegram
error) is swallowed so a flaky alert channel can never crash the monitor. A
``MultiAlerter`` fans an event out to every configured channel.
"""

from __future__ import annotations

import html
import json
import sys
import urllib.parse
import urllib.request

from .models import Event


class Alerter:
    def send(self, event: Event) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleAlerter(Alerter):
    """Prints a one-line alert — the default channel, always on."""

    def send(self, event: Event) -> None:
        line = f"{event.severity.emoji} [{event.severity.value}] {event.title}"
        if event.detail:
            line += f" — {event.detail}"
        print(line, flush=True)


class TelegramAlerter(Alerter):
    """Sends an HTML message to a chat via the Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 8.0) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, event: Event) -> None:
        text = f"{event.severity.emoji} <b>{html.escape(event.title)}</b>"
        if event.detail:
            text += f"\n{html.escape(event.detail)}"
        payload = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        ).encode()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
            # Surface a dead channel (e.g. wrong chat id) instead of silently
            # dropping alerts — but never raise out of the monitor loop.
            if not body.get("ok"):
                print(f"telegram alert rejected: {body.get('description')}",
                      file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"telegram alert failed: {exc}", file=sys.stderr, flush=True)


class MultiAlerter(Alerter):
    def __init__(self, alerters: list[Alerter]) -> None:
        self.alerters = alerters

    def send(self, event: Event) -> None:
        for alerter in self.alerters:
            try:
                alerter.send(event)
            except Exception:
                pass


def build_alerter(settings) -> Alerter:
    """Console always; Telegram added when a bot token + chat id are configured."""
    channels: list[Alerter] = [ConsoleAlerter()]
    if settings.telegram_enabled:
        channels.append(
            TelegramAlerter(settings.telegram_bot_token, settings.telegram_chat_id)
        )
    return MultiAlerter(channels)
