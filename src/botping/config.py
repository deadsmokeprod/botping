from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    admin_bot_token: str
    admin_chat_ids: tuple[int, ...]
    database_path: str
    log_level: str


def _parse_chat_ids(raw: str) -> tuple[int, ...]:
    cleaned = raw.strip().strip('"').strip("'")
    parts = [p.strip().strip('"').strip("'") for p in cleaned.replace(";", ",").split(",") if p.strip()]
    return tuple(int(p) for p in parts)


def load_settings() -> Settings:
    token = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ADMIN_BOT_TOKEN is required")

    raw_ids = os.getenv("ADMIN_CHAT_IDS", "").strip()
    if not raw_ids:
        raise RuntimeError("ADMIN_CHAT_IDS is required (comma-separated)")

    db_path = os.getenv("DATABASE_PATH", "./data/botping.db").strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    return Settings(
        admin_bot_token=token,
        admin_chat_ids=_parse_chat_ids(raw_ids),
        database_path=db_path,
        log_level=log_level,
    )
