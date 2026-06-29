"""Tests for config parsing, secret resolution, and path resolution."""

import os
import unittest
from pathlib import Path

from sentinel.config import ConfigError, Settings, _resolve_secret, parse_config


def _minimal(**overrides):
    data = {
        "targets": [
            {"name": "web", "type": "http", "target": "https://example.com"},
        ]
    }
    data.update(overrides)
    return data


class TestParseConfigDefaults(unittest.TestCase):
    def test_minimal_builds_settings_and_targets(self):
        s = parse_config(_minimal(), base_dir=Path("/base"))
        self.assertIsInstance(s, Settings)
        self.assertEqual(len(s.targets), 1)
        t = s.targets[0]
        self.assertEqual(t.name, "web")
        self.assertEqual(t.type, "http")
        self.assertEqual(t.target, "https://example.com")
        # Target defaults.
        self.assertEqual(t.timeout, 10.0)
        self.assertEqual(t.fail_threshold, 2)
        self.assertEqual(t.recover_threshold, 2)
        self.assertEqual(t.cert_warn_days, 14)

    def test_interval_default_and_floor(self):
        self.assertEqual(parse_config(_minimal(), base_dir=Path("/b")).interval_seconds, 60)
        # Floored at 5.
        s = parse_config(_minimal(interval_seconds=1), base_dir=Path("/b"))
        self.assertEqual(s.interval_seconds, 5)

    def test_type_is_lowercased(self):
        data = _minimal()
        data["targets"][0]["type"] = "HTTP"
        s = parse_config(data, base_dir=Path("/b"))
        self.assertEqual(s.targets[0].type, "http")


class TestParseConfigRejections(unittest.TestCase):
    def test_missing_targets(self):
        with self.assertRaises(ConfigError):
            parse_config({}, base_dir=Path("/b"))

    def test_empty_targets(self):
        with self.assertRaises(ConfigError):
            parse_config({"targets": []}, base_dir=Path("/b"))

    def test_targets_not_a_list(self):
        with self.assertRaises(ConfigError):
            parse_config({"targets": "nope"}, base_dir=Path("/b"))

    def test_root_not_a_dict(self):
        with self.assertRaises(ConfigError):
            parse_config(["not", "a", "dict"], base_dir=Path("/b"))

    def test_unknown_type(self):
        data = _minimal()
        data["targets"][0]["type"] = "ping"
        with self.assertRaises(ConfigError):
            parse_config(data, base_dir=Path("/b"))

    def test_duplicate_target_names(self):
        data = {
            "targets": [
                {"name": "dup", "type": "http", "target": "https://a.com"},
                {"name": "dup", "type": "tcp", "target": "b.com:22"},
            ]
        }
        with self.assertRaises(ConfigError):
            parse_config(data, base_dir=Path("/b"))

    def test_missing_required_name(self):
        data = {"targets": [{"type": "http", "target": "https://a.com"}]}
        with self.assertRaises(ConfigError):
            parse_config(data, base_dir=Path("/b"))

    def test_missing_required_target(self):
        data = {"targets": [{"name": "x", "type": "http"}]}
        with self.assertRaises(ConfigError):
            parse_config(data, base_dir=Path("/b"))

    def test_empty_name_rejected(self):
        data = {"targets": [{"name": "  ", "type": "http", "target": "https://a.com"}]}
        with self.assertRaises(ConfigError):
            parse_config(data, base_dir=Path("/b"))


class TestResolveSecret(unittest.TestCase):
    ENV_NAME = "SENTINEL_TEST_SECRET_TOKEN"

    def tearDown(self):
        os.environ.pop(self.ENV_NAME, None)

    def test_env_ref_preferred_over_literal(self):
        os.environ[self.ENV_NAME] = "from-env-value"
        section = {"bot_token": "inline-literal", "bot_token_env": self.ENV_NAME}
        self.assertEqual(
            _resolve_secret(section, "bot_token", "bot_token_env"),
            "from-env-value",
        )

    def test_env_ref_to_unset_var_yields_none(self):
        os.environ.pop(self.ENV_NAME, None)
        section = {"bot_token_env": self.ENV_NAME}
        self.assertIsNone(_resolve_secret(section, "bot_token", "bot_token_env"))

    def test_inline_literal_when_no_env_ref(self):
        section = {"bot_token": "inline-literal"}
        self.assertEqual(
            _resolve_secret(section, "bot_token", "bot_token_env"),
            "inline-literal",
        )

    def test_nothing_set_yields_none(self):
        self.assertIsNone(_resolve_secret({}, "bot_token", "bot_token_env"))


class TestTelegramEnabled(unittest.TestCase):
    ENV_TOKEN = "SENTINEL_TEST_TG_TOKEN"
    ENV_CHAT = "SENTINEL_TEST_TG_CHAT"

    def tearDown(self):
        os.environ.pop(self.ENV_TOKEN, None)
        os.environ.pop(self.ENV_CHAT, None)

    def test_enabled_when_both_present(self):
        os.environ[self.ENV_TOKEN] = "123:abc"
        os.environ[self.ENV_CHAT] = "999"
        data = _minimal(
            telegram={"bot_token_env": self.ENV_TOKEN, "chat_id_env": self.ENV_CHAT}
        )
        s = parse_config(data, base_dir=Path("/b"))
        self.assertTrue(s.telegram_enabled)

    def test_disabled_when_only_token(self):
        os.environ[self.ENV_TOKEN] = "123:abc"
        data = _minimal(telegram={"bot_token_env": self.ENV_TOKEN})
        s = parse_config(data, base_dir=Path("/b"))
        self.assertFalse(s.telegram_enabled)

    def test_disabled_when_only_chat(self):
        os.environ[self.ENV_CHAT] = "999"
        data = _minimal(telegram={"chat_id_env": self.ENV_CHAT})
        s = parse_config(data, base_dir=Path("/b"))
        self.assertFalse(s.telegram_enabled)

    def test_disabled_when_no_telegram_section(self):
        s = parse_config(_minimal(), base_dir=Path("/b"))
        self.assertFalse(s.telegram_enabled)


class TestPathResolution(unittest.TestCase):
    def test_relative_db_path_resolved_against_base_dir(self):
        base = Path("/my/base").resolve()
        s = parse_config(_minimal(db_path="data/x.db"), base_dir=base)
        self.assertEqual(Path(s.db_path), base / "data" / "x.db")

    def test_default_db_path_resolved_against_base_dir(self):
        base = Path("/my/base").resolve()
        s = parse_config(_minimal(), base_dir=base)
        self.assertEqual(Path(s.db_path), base / "sentinel.db")

    def test_absolute_db_path_left_alone(self):
        base = Path("/my/base").resolve()
        abs_path = str((Path("/elsewhere/abs.db")).resolve())
        s = parse_config(_minimal(db_path=abs_path), base_dir=base)
        self.assertEqual(Path(s.db_path), Path(abs_path))

    def test_relative_status_page_resolved(self):
        base = Path("/my/base").resolve()
        s = parse_config(_minimal(status_page="public/status.html"), base_dir=base)
        self.assertEqual(Path(s.status_page), base / "public" / "status.html")

    def test_status_page_none_by_default(self):
        s = parse_config(_minimal(), base_dir=Path("/b"))
        self.assertIsNone(s.status_page)


if __name__ == "__main__":
    unittest.main()
