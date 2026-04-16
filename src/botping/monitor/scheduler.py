from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.checker import check_getme
from botping.monitor.quiet import in_quiet_hours
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]


def _parse_sqlite_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=MOSCOW_TZ)
    except ValueError:
        return None


async def scheduler_loop(
    db: Database,
    http_client: httpx.AsyncClient,
    notify: NotifyFn,
    stop: asyncio.Event,
) -> None:
    consecutive: dict[int, int] = {}

    while not stop.is_set():
        try:
            settings = await queries.load_all_settings(db)
            interval = int(settings["check_interval_sec"])
            timeout = float(settings["request_timeout_sec"])
            fail_threshold = int(settings["fail_threshold"])
            repeat_sec = int(settings["repeat_alert_interval_sec"])
            quiet = settings.get("quiet_hours") or {}

            bots = await queries.list_monitored_bots(db)
            enabled = [b for b in bots if b["enabled"]]

            for b in enabled:
                bid = int(b["id"])
                inc = await queries.get_open_incident(db, bid)
                if inc:
                    consecutive[bid] = max(consecutive.get(bid, 0), fail_threshold)

                res = await check_getme(http_client, b["token"], timeout)
                await queries.insert_check(
                    db,
                    bid,
                    res.ok,
                    res.latency_ms,
                    res.http_status,
                    res.error_text,
                    res.rate_limited,
                )

                if res.rate_limited:
                    logger.warning("Rate limited for bot_id=%s", bid)
                    continue

                name = b["display_name"]
                quiet_down = in_quiet_hours(quiet)

                if res.ok:
                    consecutive[bid] = 0
                    open_inc = await queries.get_open_incident(db, bid)
                    if open_inc:
                        await queries.close_incident(db, int(open_inc["id"]))
                        await notify(f"Восстановлено: {name} (id={bid}). getMe снова ok.")
                else:
                    consecutive[bid] = consecutive.get(bid, 0) + 1
                    open_inc = await queries.get_open_incident(db, bid)

                    if open_inc:
                        iid = int(open_inc["id"])
                        await queries.update_incident_error(db, iid, res.error_text or "")
                        last_alert = _parse_sqlite_ts(str(open_inc["last_alert_at"]))
                        now = datetime.now(MOSCOW_TZ)
                        elapsed = (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                        if elapsed >= repeat_sec:
                            if not quiet_down:
                                await notify(
                                    f"Всё ещё недоступен: {name} (id={bid}). Ошибка: {res.error_text}"
                                )
                            await queries.touch_incident_alert(db, iid)
                    elif consecutive[bid] >= fail_threshold:
                        iid = await queries.open_incident(db, bid, res.error_text)
                        if not quiet_down:
                            await notify(
                                f"Недоступен: {name} (id={bid}). Ошибка: {res.error_text}"
                            )
                        else:
                            logger.info("Incident opened during quiet hours, alert suppressed: %s", name)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("monitor loop error")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def start_scheduler(
    db: Database,
    notify: NotifyFn,
    stop: asyncio.Event,
) -> tuple[asyncio.Task[None], httpx.AsyncClient]:
    client = httpx.AsyncClient()
    task = asyncio.create_task(scheduler_loop(db, client, notify, stop), name="botping-scheduler")
    return task, client
