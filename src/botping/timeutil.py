from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Единый пояс для меток времени в БД, Telegram и отчётах.
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def now_moscow_iso() -> str:
    """Текущий момент в часовом поясе Москва для SQLite (TEXT YYYY-MM-DD HH:MM:SS)."""
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def now_moscow_naive() -> datetime:
    """Текущее «местное» время Москвы без tzinfo (периоды / отчёт / сравнения в одном стиле с БД)."""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def seconds_until_next_midnight_moscow() -> float:
    """Секунды до 00:00:00 следующего календарного дня по Москве (минимум 0.5 с)."""
    now = datetime.now(MOSCOW_TZ)
    next_day = now.date() + timedelta(days=1)
    target = datetime.combine(next_day, time(0, 0, 0), tzinfo=MOSCOW_TZ)
    return max(0.5, (target - now).total_seconds())


def moscow_yesterday_date() -> date:
    """Вчерашняя календарная дата по Москве."""
    return datetime.now(MOSCOW_TZ).date() - timedelta(days=1)
