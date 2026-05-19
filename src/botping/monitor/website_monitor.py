from __future__ import annotations

import logging
from datetime import datetime

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.util import NotifyFn, format_age, parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)


def _ts_age_sec(ts: str | None) -> int | None:
    last = parse_sqlite_ts(ts)
    if last is None:
        return None
    now = datetime.now(MOSCOW_TZ)
    return max(0, int((now - last).total_seconds()))


def _module_alive(m: dict, hb_timeout: int) -> tuple[bool, str | None]:
    age = _ts_age_sec(m.get("last_ok_at"))
    if age is None:
        return False, "нет данных от агента"
    if age > hb_timeout:
        return False, f"устарели данные {format_age(age)}"
    err = (m.get("last_error") or "").strip()
    if err:
        return False, err
    return True, None


async def run_website_monitor_tick(
    db: Database,
    notify: NotifyFn,
    *,
    hb_timeout: int,
    fail_threshold: int,
    repeat_sec: int,
    quiet_down: bool,
    consecutive_websites: dict[int, int],
    consecutive_modules: dict[int, int],
) -> None:
    websites = await queries.list_monitored_websites(db)
    for w in websites:
        if not w["enabled"]:
            continue
        wid = int(w["id"])
        wname = str(w["display_name"])
        host = str(w["host"])
        label_base = f"{wname} ({host})"

        open_w_inc = await queries.get_open_website_incident(db, wid)
        if open_w_inc:
            consecutive_websites[wid] = max(consecutive_websites.get(wid, 0), fail_threshold)

        age = _ts_age_sec(w.get("last_heartbeat_at"))
        if age is None:
            website_alive = False
            w_err = "нет ни одного heartbeat"
        else:
            website_alive = age <= hb_timeout
            w_err = None if website_alive else f"нет heartbeat {format_age(age)}"

        if website_alive:
            consecutive_websites[wid] = 0
            if open_w_inc:
                await queries.close_website_incident(db, int(open_w_inc["id"]))
                await notify(
                    f"Восстановлено: сайт {label_base} (id={wid}). Heartbeat снова приходит."
                )
        else:
            consecutive_websites[wid] = consecutive_websites.get(wid, 0) + 1
            open_w_inc = await queries.get_open_website_incident(db, wid)
            if open_w_inc:
                iid = int(open_w_inc["id"])
                await queries.update_website_incident_error(db, iid, w_err or "")
                last_alert = parse_sqlite_ts(str(open_w_inc["last_alert_at"]))
                now = datetime.now(MOSCOW_TZ)
                elapsed = (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                if elapsed >= repeat_sec and not quiet_down:
                    await notify(
                        f"Сайт недоступен: {label_base} (id={wid}). {w_err}. "
                        "Нет push на Botping."
                    )
                if elapsed >= repeat_sec:
                    await queries.touch_website_incident_alert(db, iid)
            elif consecutive_websites[wid] >= fail_threshold:
                await queries.open_website_incident(db, wid, w_err)
                if not quiet_down:
                    await notify(f"Сайт недоступен: {label_base} (id={wid}). {w_err}.")
                else:
                    logger.info("Website incident during quiet hours: %s", wname)

        if not website_alive:
            continue

        modules = await queries.list_website_modules(db, wid, enabled_only=True)
        for m in modules:
            mid = int(m["id"])
            mname = str(m["display_name"])
            label = f"{label_base} / {mname}"

            open_m_inc = await queries.get_open_website_module_incident(db, mid)
            if open_m_inc:
                consecutive_modules[mid] = max(
                    consecutive_modules.get(mid, 0), fail_threshold
                )

            alive, err_text = _module_alive(m, hb_timeout)
            await queries.insert_website_module_check(
                db,
                mid,
                alive,
                m.get("last_latency_ms"),
                err_text,
                check_type="heartbeat_eval",
            )

            if alive:
                consecutive_modules[mid] = 0
                if open_m_inc:
                    await queries.close_website_module_incident(db, int(open_m_inc["id"]))
                    await notify(f"Восстановлено: {label}")
            else:
                consecutive_modules[mid] = consecutive_modules.get(mid, 0) + 1
                open_m_inc = await queries.get_open_website_module_incident(db, mid)
                if open_m_inc:
                    iid = int(open_m_inc["id"])
                    await queries.update_website_module_incident_error(
                        db, iid, err_text or ""
                    )
                    last_alert = parse_sqlite_ts(str(open_m_inc["last_alert_at"]))
                    now = datetime.now(MOSCOW_TZ)
                    elapsed = (
                        (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                    )
                    if elapsed >= repeat_sec and not quiet_down:
                        await notify(f"Всё ещё недоступен: {label}. {err_text}")
                    if elapsed >= repeat_sec:
                        await queries.touch_website_module_incident_alert(db, iid)
                elif consecutive_modules[mid] >= fail_threshold:
                    await queries.open_website_module_incident(db, mid, err_text)
                    if not quiet_down:
                        await notify(f"Недоступен: {label}. {err_text}")
                    else:
                        logger.info("Website module incident during quiet hours: %s", label)
