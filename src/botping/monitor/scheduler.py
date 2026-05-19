from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.checker import probe_getme_api
from botping.monitor.quiet import in_quiet_hours
from botping.monitor.router_monitor import run_router_monitor_tick
from botping.monitor.website_monitor import run_website_monitor_tick
from botping.monitor.util import NotifyFn, format_age, parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)

_parse_sqlite_ts = parse_sqlite_ts
_format_age = format_age

# Пауза после сетевой ошибки getMe — не долбить api.telegram.org при обрыве канала.
_TG_PROBE_FAILURE_BACKOFF_SEC = 180


@dataclass(frozen=True)
class _TelegramProbeOutcome:
    consecutive_failures: int
    backoff_sec: int


async def _run_telegram_api_probe(
    db: Database,
    http_client: httpx.AsyncClient,
    notify: NotifyFn,
    admin_bot_token: str,
    timeout: float,
    repeat_sec: int,
    quiet_down: bool,
    fail_threshold: int,
    consecutive_failures: int,
) -> _TelegramProbeOutcome:
    res = await probe_getme_api(http_client, admin_bot_token, timeout)
    ok = res.bot_alive is True and res.telegram_reachable
    await queries.insert_telegram_check(
        db, ok, res.latency_ms, res.http_status, res.error_text, res.rate_limited
    )

    if res.rate_limited:
        backoff = res.retry_after_sec or 60
        logger.warning(
            "Telegram API probe rate limited (429), backoff %ss", backoff
        )
        return _TelegramProbeOutcome(consecutive_failures, backoff)

    open_inc = await queries.get_open_telegram_incident(db)
    if ok:
        if open_inc:
            await queries.close_telegram_incident(db, int(open_inc["id"]))
            await notify("Восстановлено: Telegram API снова доступен (getMe ok).")
        return _TelegramProbeOutcome(0, 0)

    err = res.error_text or "telegram_unreachable"
    if open_inc:
        consecutive_failures = max(consecutive_failures, fail_threshold)
        iid = int(open_inc["id"])
        await queries.update_telegram_incident_error(db, iid, err)
        last_alert = _parse_sqlite_ts(str(open_inc["last_alert_at"]))
        now = datetime.now(MOSCOW_TZ)
        elapsed = (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
        if elapsed >= repeat_sec:
            if not quiet_down:
                await notify(f"Telegram API всё ещё недоступен. Ошибка: {err}")
            await queries.touch_telegram_incident_alert(db, iid)
        return _TelegramProbeOutcome(
            consecutive_failures, _TG_PROBE_FAILURE_BACKOFF_SEC
        )

    consecutive_failures += 1
    if consecutive_failures < fail_threshold:
        logger.debug(
            "Telegram API probe failed (%s/%s): %s",
            consecutive_failures,
            fail_threshold,
            err,
        )
        return _TelegramProbeOutcome(
            consecutive_failures, _TG_PROBE_FAILURE_BACKOFF_SEC
        )

    iid = await queries.open_telegram_incident(db, err)
    if not quiet_down:
        await notify(f"Telegram API недоступен. Ошибка: {err}")
    else:
        logger.info(
            "Telegram API incident opened during quiet hours, alert suppressed"
        )
    return _TelegramProbeOutcome(
        consecutive_failures, _TG_PROBE_FAILURE_BACKOFF_SEC
    )


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
    consecutive_tg_api = 0
    tg_probe_next_at = 0.0
    tg_backoff_until = 0.0

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
                now_mono = time.monotonic()
                if now_mono >= tg_probe_next_at and now_mono >= tg_backoff_until:
                    try:
                        outcome = await _run_telegram_api_probe(
                            db,
                            http_client,
                            notify,
                            admin_bot_token,
                            timeout,
                            repeat_sec,
                            quiet_down,
                            fail_threshold,
                            consecutive_tg_api,
                        )
                        consecutive_tg_api = outcome.consecutive_failures
                        tg_probe_next_at = now_mono + tg_probe_interval
                        if outcome.backoff_sec > 0:
                            tg_backoff_until = now_mono + outcome.backoff_sec
                    except Exception:
                        logger.exception("telegram api probe failed")
                        tg_probe_next_at = now_mono + tg_probe_interval
                        tg_backoff_until = now_mono + _TG_PROBE_FAILURE_BACKOFF_SEC

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
