"""Configuration loading (JSON, standard library only).

A config file lists the targets to watch plus a few global settings. Secrets
(the Telegram bot token) are referenced by *environment variable name* rather
than written into the file, so nothing sensitive ever lands in the repo.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import Target

_VALID_TYPES = {"http", "tcp", "tls", "dns"}


class ConfigError(ValueError):
    """Raised for a malformed or incomplete configuration."""


@dataclass
class Settings:
    """Global settings plus the resolved list of targets."""

    interval_seconds: int
    db_path: str
    status_page: Optional[str]
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]
    targets: list[Target]
    retention_days: int = 30
    # Optional TCP-connection honeypot sensor (off unless enabled in config).
    honeypot_enabled: bool = False
    honeypot_port: int = 2222
    honeypot_host: str = ""
    honeypot_banner: str = ""

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def _resolve_secret(section: dict, literal_key: str, env_key: str) -> Optional[str]:
    """Prefer an env-var reference (``*_env``) over an inline literal."""
    env_name = section.get(env_key)
    if env_name:
        return os.environ.get(env_name) or None
    value = section.get(literal_key)
    return str(value) if value else None


def _parse_target(raw: dict) -> Target:
    try:
        name = str(raw["name"]).strip()
        ttype = str(raw["type"]).strip().lower()
        target = str(raw["target"]).strip()
    except KeyError as exc:
        raise ConfigError(f"target is missing required field {exc}") from exc
    if not name or not target:
        raise ConfigError("target 'name' and 'target' must not be empty")
    if ttype not in _VALID_TYPES:
        raise ConfigError(
            f"target {name!r}: unknown type {ttype!r} "
            f"(expected one of {sorted(_VALID_TYPES)})"
        )
    return Target(
        name=name,
        type=ttype,
        target=target,
        timeout=float(raw.get("timeout", 10.0)),
        expect_status=raw.get("expect_status"),
        expect_text=raw.get("expect_text"),
        degraded_ms=raw.get("degraded_ms"),
        cert_warn_days=int(raw.get("cert_warn_days", 14)),
        expect_ip=raw.get("expect_ip"),
        fail_threshold=int(raw.get("fail_threshold", 2)),
        recover_threshold=int(raw.get("recover_threshold", 2)),
    )


def parse_config(data: dict, base_dir: Optional[Path] = None) -> Settings:
    """Build ``Settings`` from an already-parsed JSON object (pure, testable)."""
    if not isinstance(data, dict):
        raise ConfigError("config root must be a JSON object")

    targets_raw = data.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ConfigError("config must define a non-empty 'targets' array")
    targets = [_parse_target(t) for t in targets_raw]

    names = [t.name for t in targets]
    if len(set(names)) != len(names):
        raise ConfigError("target names must be unique")

    tg = data.get("telegram") or {}
    hp = data.get("honeypot") or {}
    base_dir = base_dir or Path.cwd()

    def _path(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        p = Path(value)
        return str(p if p.is_absolute() else base_dir / p)

    return Settings(
        interval_seconds=max(5, int(data.get("interval_seconds", 60))),
        db_path=_path(data.get("db_path", "sentinel.db")),
        status_page=_path(data.get("status_page")),
        telegram_bot_token=_resolve_secret(tg, "bot_token", "bot_token_env"),
        telegram_chat_id=_resolve_secret(tg, "chat_id", "chat_id_env"),
        targets=targets,
        retention_days=max(1, int(data.get("retention_days", 30))),
        honeypot_enabled=bool(hp.get("enabled", False)),
        honeypot_port=int(hp.get("port", 2222)),
        honeypot_host=str(hp.get("host", "")),
        honeypot_banner=str(hp.get("banner", "")),
    )


def load_config(path: str) -> Settings:
    """Read and parse a config file from disk."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    return parse_config(data, base_dir=p.resolve().parent)
