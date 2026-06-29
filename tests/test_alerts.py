"""Tests for alert delivery (console / multi / telegram), no real network."""

import unittest
from unittest import mock

from sentinel.alerts import (
    ConsoleAlerter,
    MultiAlerter,
    TelegramAlerter,
    build_alerter,
)
from sentinel.models import Event, Severity


def _event():
    return Event(
        kind="state_change",
        severity=Severity.CRITICAL,
        source="web",
        title="web is DOWN",
        detail="connection refused",
        ts=100.0,
    )


class _FakeSettings:
    def __init__(self, token=None, chat=None):
        self.telegram_bot_token = token
        self.telegram_chat_id = chat

    @property
    def telegram_enabled(self):
        return bool(self.telegram_bot_token and self.telegram_chat_id)


class TestBuildAlerter(unittest.TestCase):
    def test_console_only_when_telegram_disabled(self):
        alerter = build_alerter(_FakeSettings())
        self.assertIsInstance(alerter, MultiAlerter)
        self.assertEqual(len(alerter.alerters), 1)
        self.assertIsInstance(alerter.alerters[0], ConsoleAlerter)

    def test_includes_telegram_when_enabled(self):
        alerter = build_alerter(_FakeSettings(token="123:abc", chat="999"))
        self.assertIsInstance(alerter, MultiAlerter)
        self.assertEqual(len(alerter.alerters), 2)
        types = {type(a) for a in alerter.alerters}
        self.assertIn(ConsoleAlerter, types)
        self.assertIn(TelegramAlerter, types)


class TestConsoleAlerter(unittest.TestCase):
    def test_send_does_not_raise(self):
        with mock.patch("builtins.print") as mocked:
            ConsoleAlerter().send(_event())
            self.assertTrue(mocked.called)

    def test_send_includes_title(self):
        with mock.patch("builtins.print") as mocked:
            ConsoleAlerter().send(_event())
            printed = " ".join(str(c.args[0]) for c in mocked.call_args_list)
            self.assertIn("web is DOWN", printed)


class _Boom(ConsoleAlerter):
    def send(self, event):
        raise RuntimeError("alert channel exploded")


class TestMultiAlerter(unittest.TestCase):
    def test_swallows_failing_child(self):
        good = mock.Mock()
        multi = MultiAlerter([_Boom(), good])
        # Must not raise despite the failing first child.
        multi.send(_event())
        # The healthy child still receives the event.
        good.send.assert_called_once()

    def test_fans_out_to_all_children(self):
        a, b = mock.Mock(), mock.Mock()
        MultiAlerter([a, b]).send(_event())
        a.send.assert_called_once()
        b.send.assert_called_once()

    def test_empty_alerter_list_is_noop(self):
        MultiAlerter([]).send(_event())  # no raise


class TestTelegramAlerterNoNetwork(unittest.TestCase):
    def test_send_uses_urlopen_and_swallows_errors(self):
        alerter = TelegramAlerter("123:abc", "999")
        # urlopen is monkeypatched to raise; send must swallow it (no real call).
        with mock.patch(
            "sentinel.alerts.urllib.request.urlopen",
            side_effect=OSError("network blocked"),
        ) as mocked:
            alerter.send(_event())  # must not raise
            self.assertTrue(mocked.called)

    def test_send_builds_request_to_telegram_api(self):
        alerter = TelegramAlerter("TOKEN", "CHAT")
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = req.data
            return _Resp()

        with mock.patch(
            "sentinel.alerts.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            alerter.send(_event())
        self.assertIn("api.telegram.org/botTOKEN/sendMessage", captured["url"])
        self.assertIn(b"chat_id=CHAT", captured["data"])


if __name__ == "__main__":
    unittest.main()
