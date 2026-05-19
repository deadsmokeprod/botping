from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.checker import probe_getme_api
from botping.monitor.util import NotifyFn, parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)

_TG_PROBE_FAILURE_BACKOFF_SEC = 180


@dataclass
class TelegramApiProbeState:
    consecutive_failures: int = 0
    consecutive_ok: int = 0
    first_fail_mono: float | None = None
    next_probe_at: float = 0.0
    backoff_until: float = 0.0


@dataclass(frozen=True)
class TelegramApiProbeTick:
    state: TelegramApiProbeState
    backoff_sec: int = 0


async def run_telegram_api_probe_tick(
    db: Database,
    http_client: httpx.AsyncClient,
    notify: NotifyFn,
    admin_bot_token: str,
    timeout: float,
    repeat_sec: int,
    quiet_down: bool,
    *,
    fail_threshold: int,
    recover_threshold: int,
    down_alert_sec: int,
    probe_interval_sec: int,
    state: TelegramApiProbeState,
) -> TelegramApiProbeTick:
    now_mono = time.monotonic()
    if now_mono < state.next_probe_at or now_mono < state.backoff_until:
        return TelegramApiProbeTick(state=state)

    res = await probe_getme_api(http_client, admin_bot_token, timeout)
    ok = res.bot_alive is True and res.telegram_reachable
    await queries.insert_telegram_check(
        db, ok, res.latency_ms, res.http_status, res.error_text, res.rate_limited
    )

    state.next_probe_at = now_mono + probe_interval_sec

    if res.rate_limited:
        backoff = res.retry_after_sec or 60
        logger.warning("Telegram API probe rate limited (429), backoff %ss", backoff)
        state.backoff_until = now_mono + backoff
        _note_failure(state, now_mono)
        return TelegramApiProbeTick(state=state, backoff_sec=backoff)

    open_inc = await queries.get_open_telegram_incident(db)

    if ok:
        state.consecutive_failures = 0
        state.first_fail_mono = None
        state.consecutive_ok += 1

        if open_inc and state.consecutive_ok >= recover_threshold:
            iid = int(open_inc["id"])
            alerted = bool(open_inc.get("last_alert_at"))
            await queries.close_telegram_incident(db, iid)
            state.consecutive_ok = 0
            if alerted and not quiet_down:
                await notify("Восстановлено: Telegram API снова доступен (getMe ok).")
            elif alerted:
                logger.info("Telegram API recovered during quiet hours (alert suppressed)")
            else:
                logger.info(
                    "Telegram API recovered after brief flap (no alert was sent, incident %s)",
                    iid,
                )
        return TelegramApiProbeTick(state=state)

    state.consecutive_ok = 0
    err = res.error_text or "telegram_unreachable"
    _note_failure(state, now_mono)

    if open_inc:
        iid = int(open_inc["id"])
        await queries.update_telegram_incident_error(db, iid, err)
        if _should_notify_down(state, open_inc, down_alert_sec):
            if not quiet_down:
                if open_inc.get("last_alert_at"):
                    last_alert = parse_sqlite_ts(str(open_inc["last_alert_at"]))
                    now = datetime.now(MOSCOW_TZ)
                    elapsed = (
                        (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                    )
                    if elapsed >= repeat_sec:
                        await notify(
                            f"Telegram API всё ещё недоступен. Ошибка: {err}"
                        )
                        await queries.touch_telegram_incident_alert(db, iid)
                else:
                    await notify(f"Telegram API недоступен. Ошибка: {err}")
                    await queries.touch_telegram_incident_alert(db, iid)
            elif not open_inc.get("last_alert_at"):
                logger.info(
                    "Telegram API down alert suppressed (quiet hours), incident %s",
                    iid,
                )
        state.backoff_until = now_mono + _TG_PROBE_FAILURE_BACKOFF_SEC
        return TelegramApiProbeTick(
            state=state, backoff_sec=_TG_PROBE_FAILURE_BACKOFF_SEC
        )

    state.consecutive_failures += 1
    if state.consecutive_failures < fail_threshold:
        logger.debug(
            "Telegram API probe failed (%s/%s): %s",
            state.consecutive_failures,
            fail_threshold,
            err,
        )
        state.backoff_until = now_mono + _TG_PROBE_FAILURE_BACKOFF_SEC
        return TelegramApiProbeTick(
            state=state, backoff_sec=_TG_PROBE_FAILURE_BACKOFF_SEC
        )

    iid = await queries.open_telegram_incident(db, err, user_alerted=False)
    open_inc = await queries.get_open_telegram_incident(db)
    assert open_inc is not None
    if _should_notify_down(state, open_inc, down_alert_sec):
        if not quiet_down:
            await notify(f"Telegram API недоступен. Ошибка: {err}")
            await queries.touch_telegram_incident_alert(db, iid)
        else:
            logger.info(
                "Telegram API incident opened during quiet hours, alert suppressed"
            )
    else:
        logger.info(
            "Telegram API incident %s opened (waiting %ss before alert)",
            iid,
            down_alert_sec,
        )

    state.backoff_until = now_mono + _TG_PROBE_FAILURE_BACKOFF_SEC
    return TelegramApiProbeTick(state=state, backoff_sec=_TG_PROBE_FAILURE_BACKOFF_SEC)


def _note_failure(state: TelegramApiProbeState, now_mono: float) -> None:
    if state.first_fail_mono is None:
        state.first_fail_mono = now_mono


def _should_notify_down(
    state: TelegramApiProbeState,
    open_inc: dict,
    down_alert_sec: int,
) -> bool:
    if open_inc.get("last_alert_at"):
        return True
    if state.first_fail_mono is None:
        return False
    return (time.monotonic() - state.first_fail_mono) >= down_alert_sec
