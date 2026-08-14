"""
config.py — Environment configuration for Toss Forward Bot.

All secrets are loaded from environment variables (via python-dotenv in dev).
Nothing here is hard-coded; nothing here is logged.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default=None):
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got: {val!r}")


def _get_required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


@dataclass(frozen=True)
class Settings:
    # Bot account (admin/control + posts to target channel)
    bot_token: str

    # Telethon user account (monitors source channel)
    api_id: int
    api_hash: str
    session_name: str

    # Access control
    admin_user_id: int

    # Channels — may be unset at first boot, configured later via /setsource /settarget
    source_channel_id: int | None
    target_channel_id: int | None

    # Paths
    db_path: str
    log_path: str


def load_settings() -> Settings:
    return Settings(
        bot_token=_get_required("BOT_TOKEN"),
        api_id=_get_int("TELEGRAM_API_ID") or _raise_missing("TELEGRAM_API_ID"),
        api_hash=_get_required("TELEGRAM_API_HASH"),
        session_name=os.getenv("TELETHON_SESSION", "toss_forward_user"),
        admin_user_id=_get_int("ADMIN_USER_ID") or _raise_missing("ADMIN_USER_ID"),
        source_channel_id=_get_int("SOURCE_CHANNEL_ID"),
        target_channel_id=_get_int("TARGET_CHANNEL_ID"),
        db_path=os.getenv("DB_PATH", "toss_forward.db"),
        log_path=os.getenv("LOG_PATH", "toss_forward.log"),
    )


def _raise_missing(name: str):
    raise RuntimeError(f"Missing required environment variable: {name}")


settings = load_settings()
