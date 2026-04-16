from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo


def in_quiet_hours(quiet: dict[str, Any], now: datetime | None = None) -> bool:
    if not quiet or not isinstance(quiet, dict):
        return False
    start_s = quiet.get("start")
    end_s = quiet.get("end")
    if not start_s or not end_s:
        return False
    tz_name = str(quiet.get("tz") or "Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Moscow")

    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    def parse_hm(s: str) -> dtime:
        parts = str(s).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return dtime(h, m)

    t_start = parse_hm(str(start_s))
    t_end = parse_hm(str(end_s))
    cur = dtime(now.hour, now.minute, now.second)

    if t_start <= t_end:
        return t_start <= cur <= t_end
    return cur >= t_start or cur <= t_end
