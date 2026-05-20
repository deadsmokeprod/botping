from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from botping.db.monitor_settings import EffectiveMonitorSettings
from botping.monitor.quiet import in_quiet_hours
from botping.monitor.util import NotifyFn, parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)

OpenIncidentFn = Callable[[], Awaitable[dict[str, Any] | None]]
GetOpenIncidentFn = OpenIncidentFn
OpenNewIncidentFn = Callable[[str | None], Awaitable[int]]
CloseIncidentFn = Callable[[int], Awaitable[None]]
UpdateErrorFn = Callable[[int, str], Awaitable[None]]
TouchAlertFn = Callable[[int], Awaitable[None]]


@dataclass
class EntityTickState:
    consecutive_fail: int = 0
    consecutive_ok: int = 0
    first_fail_mono: float | None = None
    consecutive_slow: int = 0
    consecutive_slow_ok: int = 0
    first_slow_mono: float | None = None
    slow_open: bool = False
    slow_last_alert_mono: float = 0.0


@dataclass
class EntityMonitorState:
    entities: dict[tuple[str, int], EntityTickState] = field(default_factory=dict)

    def get(self, kind: str, entity_id: int) -> EntityTickState:
        key = (kind, entity_id)
        if key not in self.entities:
            self.entities[key] = EntityTickState()
        return self.entities[key]


def _should_notify_down(state: EntityTickState, open_inc: dict[str, Any], down_alert_sec: int) -> bool:
    if open_inc.get("last_alert_at"):
        return True
    if state.first_fail_mono is None:
        return False
    return (time.monotonic() - state.first_fail_mono) >= down_alert_sec


def _should_notify_slow(state: EntityTickState, eff: EffectiveMonitorSettings) -> bool:
    if not state.slow_open:
        return False
    if state.slow_last_alert_mono <= 0:
        if state.first_slow_mono is None:
            return False
        return (time.monotonic() - state.first_slow_mono) >= eff.down_alert_sec
    return (time.monotonic() - state.slow_last_alert_mono) >= eff.repeat_alert_interval_sec


async def process_entity_tick(
    state: EntityTickState,
    *,
    is_down: bool,
    is_slow: bool,
    latency_ms: int | None,
    err_text: str | None,
    label: str,
    eff: EffectiveMonitorSettings,
    global_quiet_down: bool,
    notify: NotifyFn,
    get_open_incident: GetOpenIncidentFn,
    open_incident: OpenNewIncidentFn,
    close_incident: CloseIncidentFn,
    update_incident_error: UpdateErrorFn,
    touch_incident_alert: TouchAlertFn,
    down_prefix: str = "Недоступен",
    down_repeat_prefix: str = "Всё ещё недоступен",
    recover_suffix: str = "",
) -> None:
    """Единая логика down/slow/recover как у Telegram API probe."""
    quiet_entity = in_quiet_hours(eff.quiet_hours) if eff.quiet_hours else False
    quiet_down = quiet_entity or global_quiet_down
    now_mono = time.monotonic()

    if is_down:
        state.consecutive_ok = 0
        state.consecutive_slow = 0
        state.consecutive_slow_ok = 0
        state.slow_open = False
        state.first_slow_mono = None

        if state.first_fail_mono is None:
            state.first_fail_mono = now_mono
        state.consecutive_fail += 1

        open_inc = await get_open_incident()
        if open_inc:
            state.consecutive_fail = max(state.consecutive_fail, eff.fail_threshold)
            iid = int(open_inc["id"])
            await update_incident_error(iid, err_text or "")
            if _should_notify_down(state, open_inc, eff.down_alert_sec):
                if not quiet_down:
                    last_alert = parse_sqlite_ts(str(open_inc["last_alert_at"]))
                    now = datetime.now(MOSCOW_TZ)
                    elapsed = (
                        (now - last_alert).total_seconds()
                        if last_alert
                        else eff.repeat_alert_interval_sec + 1
                    )
                    if open_inc.get("last_alert_at") and elapsed >= eff.repeat_alert_interval_sec:
                        await notify(f"{down_repeat_prefix}: {label}. {err_text}")
                        await touch_incident_alert(iid)
                    elif not open_inc.get("last_alert_at"):
                        await notify(f"{down_repeat_prefix}: {label}. {err_text}")
                        await touch_incident_alert(iid)
                elif not open_inc.get("last_alert_at"):
                    logger.info("Down alert suppressed (quiet hours): %s", label)
        elif state.consecutive_fail >= eff.fail_threshold:
            iid = await open_incident(err_text)
            open_inc = await get_open_incident()
            assert open_inc is not None
            if _should_notify_down(state, open_inc, eff.down_alert_sec):
                if not quiet_down:
                    await notify(f"{down_prefix}: {label}. {err_text}")
                    await touch_incident_alert(iid)
                else:
                    logger.info("Incident opened during quiet hours: %s", label)
            else:
                logger.info(
                    "Incident %s opened, waiting %ss before alert: %s",
                    iid,
                    eff.down_alert_sec,
                    label,
                )
        return

    # not down
    state.consecutive_fail = 0
    state.first_fail_mono = None
    state.consecutive_ok += 1

    open_inc = await get_open_incident()
    if open_inc and state.consecutive_ok >= eff.recover_threshold:
        iid = int(open_inc["id"])
        alerted = bool(open_inc.get("last_alert_at"))
        await close_incident(iid)
        state.consecutive_ok = 0
        if alerted and not quiet_down:
            msg = f"Восстановлено: {label}"
            if recover_suffix:
                msg += f" {recover_suffix}"
            await notify(msg)
        elif alerted:
            logger.info("Recovery during quiet hours: %s", label)

    slow_enabled = eff.slow_ms > 0
    if slow_enabled and is_slow:
        state.consecutive_slow_ok = 0
        if state.first_slow_mono is None:
            state.first_slow_mono = now_mono
        state.consecutive_slow += 1

        if state.consecutive_slow >= eff.fail_threshold:
            if not state.slow_open:
                state.slow_open = True
                state.slow_last_alert_mono = 0.0
            if _should_notify_slow(state, eff) and not quiet_down:
                ms = latency_ms if latency_ms is not None else 0
                await notify(
                    f"Медленный ответ: {label}. {ms} ms (порог {eff.slow_ms} ms)"
                )
                state.slow_last_alert_mono = now_mono
    elif slow_enabled:
        state.consecutive_slow = 0
        state.first_slow_mono = None
        state.consecutive_slow_ok += 1
        if state.slow_open and state.consecutive_slow_ok >= eff.recover_threshold:
            if not quiet_down:
                await notify(f"Пинг нормализовался: {label}")
            state.slow_open = False
            state.slow_last_alert_mono = 0.0
            state.consecutive_slow_ok = 0
