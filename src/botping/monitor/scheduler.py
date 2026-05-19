from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.quiet import in_quiet_hours
from botping.monitor.router_monitor import run_router_monitor_tick
from botping.monitor.telegram_api_monitor import (
    TelegramApiProbeState,
    run_telegram_api_probe_tick,
)
from botping.monitor.website_monitor import run_website_monitor_tick
from botping.monitor.util import NotifyFn, format_age, parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)

_parse_sqlite_ts = parse_sqlite_ts
_format_age = format_age


def _heartbeat_age_sec(bot: dict) -> int | None:
    last = _parse_sqlite_ts(bot.get("last_heartbeat_at"))
    if last is None:
        return None
    now = datetime.now(MOSCOW_TZ)
    return max(0, int((now - last).total_seconds()))


async def scheduler_loop(
    db: Database,
    http_client: httpx.AsyncClient,
    notify: NotifyFn,
    stop: asyncio.Event,
    admin_bot_token: str,
) -> None:
    consecutive: dict[int, int] = {}
    consecutive_routers: dict[int, int] = {}
    consecutive_targets: dict[int, int] = {}
    consecutive_websites: dict[int, int] = {}
    consecutive_modules: dict[int, int] = {}
    tg_probe_state = TelegramApiProbeState()

    while not stop.is_set():
        try:
            settings = await queries.load_all_settings(db)
            interval = int(settings["check_interval_sec"])
            timeout = float(settings["request_timeout_sec"])
            fail_threshold = int(settings["fail_threshold"])
            repeat_sec = int(settings["repeat_alert_interval_sec"])
            quiet = settings.get("quiet_hours") or {}
            tg_probe_on = bool(settings.get("telegram_api_probe_enabled", True))
            tg_probe_interval = int(settings["telegram_api_check_interval_sec"])
            hb_timeout = int(settings["heartbeat_timeout_sec"])
            quiet_down = in_quiet_hours(quiet)

            if tg_probe_on and admin_bot_token:
                try:
                    tick = await run_telegram_api_probe_tick(
                        db,
                        http_client,
                        notify,
                        admin_bot_token,
                        timeout,
                        repeat_sec,
                        quiet_down,
                        fail_threshold=int(settings["telegram_api_fail_threshold"]),
                        recover_threshold=int(settings["telegram_api_recover_threshold"]),
                        down_alert_sec=int(settings["telegram_api_down_alert_sec"]),
                        probe_interval_sec=tg_probe_interval,
                        state=tg_probe_state,
                    )
                    tg_probe_state = tick.state
                except Exception:
                    logger.exception("telegram api probe failed")

            bots = await queries.list_monitored_bots(db)
            enabled = [b for b in bots if b["enabled"]]

            for b in enabled:
                bid = int(b["id"])
                inc = await queries.get_open_incident(db, bid)
                if inc:
                    consecutive[bid] = max(consecutive.get(bid, 0), fail_threshold)

                age = _heartbeat_age_sec(b)
                if age is None:
                    alive = False
                    err_text = "нет ни одного heartbeat"
                    latency_val: int | None = None
                else:
                    alive = age <= hb_timeout
                    latency_val = age * 1000
                    err_text = None if alive else f"нет heartbeat {_format_age(age)}"

                await queries.insert_check(
                    db,
                    bid,
                    alive,
                    latency_val,
                    None,
                    err_text,
                    False,
                    check_type="heartbeat",
                )

                name = b["display_name"]

                if alive:
                    consecutive[bid] = 0
                    open_inc = await queries.get_open_incident(db, bid)
                    if open_inc:
                        await queries.close_incident(db, int(open_inc["id"]))
                        await notify(
                            f"Восстановлено: {name} (id={bid}). Heartbeat снова приходит."
                        )
                else:
                    consecutive[bid] = consecutive.get(bid, 0) + 1
                    open_inc = await queries.get_open_incident(db, bid)

                    if open_inc:
                        iid = int(open_inc["id"])
                        await queries.update_incident_error(db, iid, err_text or "")
                        last_alert = _parse_sqlite_ts(str(open_inc["last_alert_at"]))
                        now = datetime.now(MOSCOW_TZ)
                        elapsed = (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                        if elapsed >= repeat_sec:
                            if not quiet_down:
                                await notify(
                                    f"Всё ещё недоступен: {name} (id={bid}). "
                                    f"{err_text}. Похоже, процесс бота остановлен "
                                    f"или у сервера бота нет интернета."
                                )
                            await queries.touch_incident_alert(db, iid)
                    elif consecutive[bid] >= fail_threshold:
                        iid = await queries.open_incident(db, bid, err_text)
                        if not quiet_down:
                            await notify(
                                f"Недоступен: {name} (id={bid}). {err_text}. "
                                f"Похоже, процесс бота остановлен или у сервера бота нет интернета."
                            )
                        else:
                            logger.info(
                                "Incident opened during quiet hours, alert suppressed: %s",
                                name,
                            )

            try:
                await run_router_monitor_tick(
                    db,
                    notify,
                    hb_timeout=hb_timeout,
                    fail_threshold=fail_threshold,
                    repeat_sec=repeat_sec,
                    quiet_down=quiet_down,
                    consecutive_routers=consecutive_routers,
                    consecutive_targets=consecutive_targets,
                )
            except Exception:
                logger.exception("router monitor tick failed")

            try:
                await run_website_monitor_tick(
                    db,
                    notify,
                    hb_timeout=hb_timeout,
                    fail_threshold=fail_threshold,
                    repeat_sec=repeat_sec,
                    quiet_down=quiet_down,
                    consecutive_websites=consecutive_websites,
                    consecutive_modules=consecutive_modules,
                )
            except Exception:
                logger.exception("website monitor tick failed")

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
    admin_bot_token: str,
) -> tuple[asyncio.Task[None], httpx.AsyncClient]:
    client = httpx.AsyncClient()
    task = asyncio.create_task(
        scheduler_loop(db, client, notify, stop, admin_bot_token),
        name="botping-scheduler",
    )
    return task, client
