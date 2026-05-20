from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.entity_incident import EntityMonitorState, process_entity_tick
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
    monitor_state = EntityMonitorState()
    tg_probe_state = TelegramApiProbeState()

    while not stop.is_set():
        try:
            settings = await queries.load_all_settings(db)
            interval = int(settings["check_interval_sec"])
            timeout = float(settings["request_timeout_sec"])
            repeat_sec = int(settings["repeat_alert_interval_sec"])
            quiet = settings.get("quiet_hours") or {}
            tg_probe_on = bool(settings.get("telegram_api_probe_enabled", True))
            tg_probe_interval = int(settings["telegram_api_check_interval_sec"])
            global_quiet_down = in_quiet_hours(quiet)

            if tg_probe_on and admin_bot_token:
                try:
                    tick = await run_telegram_api_probe_tick(
                        db,
                        http_client,
                        notify,
                        admin_bot_token,
                        timeout,
                        repeat_sec,
                        global_quiet_down,
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
                name = b["display_name"]
                eff = queries.effective_monitor_for_entity(settings, b)
                st = monitor_state.get("bot", bid)

                age = _heartbeat_age_sec(b)
                if age is None:
                    alive = False
                    err_text = "нет ни одного heartbeat"
                    latency_val: int | None = None
                else:
                    alive = age <= eff.heartbeat_timeout_sec
                    latency_val = age * 1000
                    err_text = None if alive else f"нет heartbeat {_format_age(age)}"

                is_slow = (
                    alive
                    and eff.slow_ms > 0
                    and latency_val is not None
                    and latency_val > eff.slow_ms
                )

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

                async def _get_open(bid: int = bid) -> dict | None:
                    return await queries.get_open_incident(db, bid)

                await process_entity_tick(
                    st,
                    is_down=not alive,
                    is_slow=is_slow,
                    latency_ms=latency_val,
                    err_text=err_text,
                    label=f"{name} (id={bid})",
                    eff=eff,
                    global_quiet_down=global_quiet_down,
                    notify=notify,
                    get_open_incident=_get_open,
                    open_incident=lambda err, bid=bid: queries.open_incident(db, bid, err),
                    close_incident=lambda iid: queries.close_incident(db, iid),
                    update_incident_error=lambda iid, err: queries.update_incident_error(
                        db, iid, err
                    ),
                    touch_incident_alert=lambda iid: queries.touch_incident_alert(db, iid),
                    down_repeat_prefix="Всё ещё недоступен",
                    recover_suffix="Heartbeat снова приходит.",
                )

            try:
                await run_router_monitor_tick(
                    db,
                    notify,
                    global_settings=settings,
                    global_quiet_down=global_quiet_down,
                    monitor_state=monitor_state,
                )
            except Exception:
                logger.exception("router monitor tick failed")

            try:
                await run_website_monitor_tick(
                    db,
                    notify,
                    global_settings=settings,
                    global_quiet_down=global_quiet_down,
                    monitor_state=monitor_state,
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
