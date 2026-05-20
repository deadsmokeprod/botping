from __future__ import annotations

import os
from datetime import datetime

from typing import Any

from botping.db import queries
from botping.timeutil import MOSCOW_TZ


def effective_hb_timeout_sec(
    global_settings: dict[str, Any],
    entity_row: dict[str, Any],
    *,
    parent_row: dict[str, Any] | None = None,
) -> int:
    """Таймаут heartbeat: глобальный → override родителя (сайт) → override объекта."""
    return queries.effective_monitor_for_entity(
        global_settings, entity_row, parent_row=parent_row
    ).heartbeat_timeout_sec


def mask_secret(secret: str) -> str:
    s = (secret or "").strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}…{s[-4:]}"


def format_age_ru(sec: int) -> str:
    if sec < 60:
        return f"{sec} с"
    if sec < 3600:
        return f"{sec // 60} мин"
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h} ч {m} мин" if m else f"{h} ч"


def heartbeat_age_sec(entity: dict) -> int | None:
    raw = entity.get("last_heartbeat_at")
    if not raw:
        return None
    try:
        naive = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
        last = naive.replace(tzinfo=MOSCOW_TZ)
    except ValueError:
        return None
    now = datetime.now(MOSCOW_TZ)
    return max(0, int((now - last).total_seconds()))


def public_host_from_env() -> str:
    url = os.getenv("BOTPING_PUBLIC_URL", "").strip().rstrip("/")
    if url:
        return url
    host = os.getenv("BOTPING_PUBLIC_HOST", "").strip()
    port = os.getenv("HEARTBEAT_PORT", "8080").strip() or "8080"
    if host:
        return f"http://{host}:{port}"
    return "http://<IP_VPS>:{port}"


def chunk_text(s: str, limit: int = 3900) -> list[str]:
    if len(s) <= limit:
        return [s]
    parts: list[str] = []
    cur = ""
    for line in s.splitlines():
        if len(cur) + len(line) + 1 > limit:
            if cur:
                parts.append(cur)
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur:
        parts.append(cur)
    return parts
