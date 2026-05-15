from __future__ import annotations

import logging
from datetime import datetime

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.scheduler import NotifyFn, _format_age, _parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)


def _ts_age_sec(ts: str | None) -> int | None:
    last = _parse_sqlite_ts(ts)
    if last is None:
        return None
    now = datetime.now(MOSCOW_TZ)
    return max(0, int((now - last).total_seconds()))


def _target_alive(t: dict, hb_timeout: int) -> tuple[bool, str | None]:
    age = _ts_age_sec(t.get("last_ok_at"))
    if age is None:
        return False, "нет данных от роутера"
    if age > hb_timeout:
        return False, f"устарели данные {_format_age(age)}"
    err = (t.get("last_error") or "").strip()
    if err:
        return False, err
    return True, None


async def run_router_monitor_tick(
    db: Database,
    notify: NotifyFn,
    *,
    hb_timeout: int,
    fail_threshold: int,
    repeat_sec: int,
    quiet_down: bool,
    consecutive_routers: dict[int, int],
    consecutive_targets: dict[int, int],
) -> None:
    routers = await queries.list_monitored_routers(db)
    for r in routers:
        if not r["enabled"]:
            continue
        rid = int(r["id"])
        rname = str(r["display_name"])
        open_r_inc = await queries.get_open_router_incident(db, rid)
        if open_r_inc:
            consecutive_routers[rid] = max(consecutive_routers.get(rid, 0), fail_threshold)

        age = _ts_age_sec(r.get("last_heartbeat_at"))
        if age is None:
            router_alive = False
            r_err = "нет ни одного heartbeat"
        else:
            router_alive = age <= hb_timeout
            r_err = None if router_alive else f"нет heartbeat {_format_age(age)}"

        if router_alive:
            consecutive_routers[rid] = 0
            if open_r_inc:
                await queries.close_router_incident(db, int(open_r_inc["id"]))
                await notify(
                    f"Восстановлено: роутер {rname} (id={rid}). Heartbeat снова приходит."
                )
        else:
            consecutive_routers[rid] = consecutive_routers.get(rid, 0) + 1
            open_r_inc = await queries.get_open_router_incident(db, rid)
            if open_r_inc:
                iid = int(open_r_inc["id"])
                await queries.update_router_incident_error(db, iid, r_err or "")
                last_alert = _parse_sqlite_ts(str(open_r_inc["last_alert_at"]))
                now = datetime.now(MOSCOW_TZ)
                elapsed = (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                if elapsed >= repeat_sec and not quiet_down:
                    await notify(
                        f"Роутер недоступен: {rname} (id={rid}). {r_err}. "
                        "Нет push на Botping."
                    )
                if elapsed >= repeat_sec:
                    await queries.touch_router_incident_alert(db, iid)
            elif consecutive_routers[rid] >= fail_threshold:
                await queries.open_router_incident(db, rid, r_err)
                if not quiet_down:
                    await notify(f"Роутер недоступен: {rname} (id={rid}). {r_err}.")
                else:
                    logger.info("Router incident during quiet hours: %s", rname)

        if not router_alive:
            continue

        targets = await queries.list_router_targets(db, rid, enabled_only=True)
        for t in targets:
            tid = int(t["id"])
            tname = str(t["display_name"])
            addr = str(t["address"])
            label = f"{rname} / {tname} ({addr})"

            open_t_inc = await queries.get_open_router_target_incident(db, tid)
            if open_t_inc:
                consecutive_targets[tid] = max(
                    consecutive_targets.get(tid, 0), fail_threshold
                )

            alive, err_text = _target_alive(t, hb_timeout)
            await queries.insert_router_target_check(
                db,
                tid,
                alive,
                t.get("last_latency_ms"),
                err_text,
                check_type="heartbeat_eval",
            )

            if alive:
                consecutive_targets[tid] = 0
                if open_t_inc:
                    await queries.close_router_target_incident(db, int(open_t_inc["id"]))
                    await notify(f"Восстановлено: {label}")
            else:
                consecutive_targets[tid] = consecutive_targets.get(tid, 0) + 1
                open_t_inc = await queries.get_open_router_target_incident(db, tid)
                if open_t_inc:
                    iid = int(open_t_inc["id"])
                    await queries.update_router_target_incident_error(
                        db, iid, err_text or ""
                    )
                    last_alert = _parse_sqlite_ts(str(open_t_inc["last_alert_at"]))
                    now = datetime.now(MOSCOW_TZ)
                    elapsed = (
                        (now - last_alert).total_seconds() if last_alert else repeat_sec + 1
                    )
                    if elapsed >= repeat_sec and not quiet_down:
                        await notify(f"Всё ещё недоступен: {label}. {err_text}")
                    if elapsed >= repeat_sec:
                        await queries.touch_router_target_incident_alert(db, iid)
                elif consecutive_targets[tid] >= fail_threshold:
                    await queries.open_router_target_incident(db, tid, err_text)
                    if not quiet_down:
                        await notify(f"Недоступен: {label}. {err_text}")
                    else:
                        logger.info("Target incident during quiet hours: %s", label)
