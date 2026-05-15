from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from botping.timeutil import MOSCOW_TZ

NotifyFn = Callable[[str], Awaitable[None]]


def parse_sqlite_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=MOSCOW_TZ)
    except ValueError:
        return None


def format_age(sec: int) -> str:
    if sec < 60:
        return f"{sec} с"
    if sec < 3600:
        return f"{sec // 60} мин"
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h} ч {m} мин" if m else f"{h} ч"
