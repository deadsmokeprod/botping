from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.checker import probe_getme_api, probe_getupdates
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


async def _run_telegram_api_probe(
    db: Database,
    http_client: httpx.AsyncClient,
    notify: NotifyFn,
    admin_bot_token: str,
    timeout: float,
    repeat_sec: int,
    quiet_down: bool,
) -> None:
    res = await probe_getme_api(http_client, admin_bot_token, timeout)
    ok = res.bot_alive is True and res.telegram_reachable
    await queries.insert_telegram_check(
        db,
        ok,
        res.latency_ms,
        res.http_status,
        res.error_text,
        res.rate_limited,
    )

    if res.rate_limited:
        logger.warning("Telegram API probe rate limited")
        return

    open_inc = await queries.get_open_telegram_incident(db)
    if ok:
        if open_inc:
            await queries.close_telegram_incident(db, int(open_inc["id"]))
            await notify("Восстановлено: Telegram API снова доступен (getMe ok).")
        return

    err = res.error_text or "telegram_unreachable"
    if open_inc:
        iid = int(open_inc["id"])
        await queries.update_telegram_incident_error(db, iid, err)
        last_alert = _parse_sqlite_ts(str(open_inc["last_alert_at"]))
        now = datetime.now(MOSCOW_TZ)
        elapsed = (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
        if elapsed >= repeat_sec:
            if not quiet_down:
                await notify(f"Telegram API всё ещё недоступен. Ошибка: {err}")
            await queries.touch_telegram_incident_alert(db, iid)
    else:
        iid = await queries.open_telegram_incident(db, err)
        if not quiet_down:
            await notify(f"Telegram API недоступен. Ошибка: {err}")
        else:
            logger.info(
                "Telegram API incident opened during quiet hours, alert suppressed"
            )


async def scheduler_loop(
    db: Database,
    http_client: httpx.AsyncClient,
    notify: NotifyFn,
    stop: asyncio.Event,
    admin_bot_token: str,
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
            tg_probe_on = bool(settings.get("telegram_api_probe_enabled", True))
            quiet_down = in_quiet_hours(quiet)

            if tg_probe_on and admin_bot_token:
                try:
                    await _run_telegram_api_probe(
                        db,
                        http_client,
                        notify,
                        admin_bot_token,
                        timeout,
                        repeat_sec,
                        quiet_down,
                    )
                except Exception:
                    logger.exception("telegram api probe failed")

            bots = await queries.list_monitored_bots(db)
            enabled = [b for b in bots if b["enabled"]]

            for b in enabled:
                bid = int(b["id"])
                inc = await queries.get_open_incident(db, bid)
                if inc:
                    consecutive[bid] = max(consecutive.get(bid, 0), fail_threshold)

                res = await probe_getupdates(http_client, b["token"], timeout)

                # Записываем проверку всегда. Для "Telegram недоступен" фиксируем
                # факт неудачного запроса (ok=0, error_text), но НЕ увеличиваем
                # счётчик падений бота — это вина сети Botping↔Telegram, её
                # поймает отдельная telegram_api-проверка.
                if res.telegram_reachable:
                    row_ok = res.bot_alive is True
                else:
                    row_ok = False
                await queries.insert_check(
                    db,
                    bid,
                    row_ok,
                    res.latency_ms,
                    res.http_status,
                    res.error_text,
                    res.rate_limited,
                    check_type="getupdates",
                )

                if res.rate_limited:
                    logger.warning("Rate limited for bot_id=%s", bid)
                    continue

                name = b["display_name"]

                if not res.telegram_reachable:
                    logger.info(
                        "Telegram unreachable for bot_id=%s: %s (не влияет на bot-инцидент)",
                        bid,
                        res.error_text,
                    )
                    continue

                if res.bot_alive is True:
                    consecutive[bid] = 0
                    open_inc = await queries.get_open_incident(db, bid)
                    if open_inc:
                        await queries.close_incident(db, int(open_inc["id"]))
                        await notify(
                            f"Восстановлено: {name} (id={bid}). Бот снова ведёт getUpdates."
                        )
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
                                    f"Всё ещё недоступен: {name} (id={bid}). "
                                    f"Бот не ведёт getUpdates (процесс не запущен или нет интернета на сервере бота). "
                                    f"Ошибка: {res.error_text}"
                                )
                            await queries.touch_incident_alert(db, iid)
                    elif consecutive[bid] >= fail_threshold:
                        iid = await queries.open_incident(db, bid, res.error_text)
                        if not quiet_down:
                            await notify(
                                f"Недоступен: {name} (id={bid}). "
                                f"Бот не ведёт getUpdates (процесс не запущен или нет интернета на сервере бота). "
                                f"Ошибка: {res.error_text}"
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
    admin_bot_token: str,
) -> tuple[asyncio.Task[None], httpx.AsyncClient]:
    client = httpx.AsyncClient()
    task = asyncio.create_task(
        scheduler_loop(db, client, notify, stop, admin_bot_token),
        name="botping-scheduler",
    )
    return task, client
