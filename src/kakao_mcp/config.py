"""Configuration loading.

Config lives outside the repo, at ``~/.config/kakao-mcp/config.json`` (override
with ``KAKAO_MCP_CONFIG``). Everything is optional; sending is disabled unless
the file explicitly enables it and lists allowed chats.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "kakao-mcp" / "config.json"
DEPS_BIN_DIR = Path.home() / ".local" / "share" / "kakao-mcp" / "bin"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ReadConfig:
    exclude_chat_ids: frozenset[int] = frozenset()
    # Messages per page when the caller doesn't pass a limit, and the most a
    # caller may ask for in one page. Older pages are reached with cursors.
    default_messages: int = 100
    max_messages: int = 1000


@dataclass(frozen=True)
class MediaConfig:
    max_download_mb: int = 200


@dataclass(frozen=True)
class SendConfig:
    enabled: bool = False
    # alias -> exact KakaoTalk chat name that kmsg searches for
    allowed_chats: dict[str, str] = field(default_factory=dict)
    max_length: int = 1000
    confirm_ttl_seconds: int = 300


@dataclass(frozen=True)
class Config:
    kakaocli_path: str | None
    kmsg_path: str | None
    read: ReadConfig
    send: SendConfig
    media: MediaConfig = MediaConfig()
    # KakaoTalk internal numeric user id. Only needed when kakaocli cannot
    # auto-detect it; kakao-mcp then derives the DB key and passes --db/--key.
    user_id: int | None = None


def _resolve_binary(configured: str | None, name: str) -> str | None:
    """Pick the binary: explicit config > install-deps.sh output > PATH."""
    if configured:
        return str(Path(configured).expanduser())
    local = DEPS_BIN_DIR / name
    if local.is_file():
        return str(local)
    return shutil.which(name)


def load_config(path: Path | None = None) -> Config:
    if path is None:
        env = os.environ.get("KAKAO_MCP_CONFIG", "").strip()
        path = Path(env).expanduser() if env else DEFAULT_CONFIG_PATH

    raw: dict = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError(f"Failed to read config {path}: {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError(f"Config {path} must be a JSON object")

    read_raw = raw.get("read") or {}
    send_raw = raw.get("send") or {}

    try:
        read = ReadConfig(
            exclude_chat_ids=frozenset(int(x) for x in read_raw.get("exclude_chat_ids", [])),
            default_messages=int(read_raw.get("default_messages", 100)),
            max_messages=int(read_raw.get("max_messages", 1000)),
        )
        if not 1 <= read.default_messages <= read.max_messages <= 5000:
            raise ConfigError("need 1 <= read.default_messages <= read.max_messages <= 5000")
        media = MediaConfig(max_download_mb=int((raw.get("media") or {}).get("max_download_mb", 200)))
        allowed = send_raw.get("allowed_chats", {})
        if not isinstance(allowed, dict):
            raise ConfigError("send.allowed_chats must be an object of {alias: chat name}")
        send = SendConfig(
            enabled=send_raw.get("enabled", False) is True,
            allowed_chats={str(k).strip(): str(v).strip() for k, v in allowed.items() if str(v).strip()},
            max_length=int(send_raw.get("max_length", 1000)),
            confirm_ttl_seconds=int(send_raw.get("confirm_ttl_seconds", 300)),
        )
        user_id = raw.get("user_id")
        user_id = int(user_id) if user_id not in (None, "") else None
    except (TypeError, ValueError) as e:
        raise ConfigError(f"Invalid value in config {path}: {e}") from e

    return Config(
        kakaocli_path=_resolve_binary(raw.get("kakaocli_path"), "kakaocli"),
        kmsg_path=_resolve_binary(raw.get("kmsg_path"), "kmsg"),
        read=read,
        send=send,
        media=media,
        user_id=user_id,
    )
