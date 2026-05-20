from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from botping.db import queries
from botping.db.pool import Database
from botping.monitor.entity_incident import EntityMonitorState, process_entity_tick
from botping.monitor.util import NotifyFn, format_age, parse_sqlite_ts
from botping.timeutil import MOSCOW_TZ

logger = logging.getLogger(__name__)


def _ts_age_sec(ts: str | None) -> int | None:
    last = parse_sqlite_ts(ts)
    if last is None:
        return None
    now = datetime.now(MOSCOW_TZ)
    return max(0, int((now - last).total_seconds()))


def _target_alive(t: dict, hb_timeout: int) -> tuple[bool, str | None, int | None]:
    age = _ts_age_sec(t.get("last_ok_at"))
    if age is None:
        return False, "нет данных от роутера", None
    if age > hb_timeout:
        return False, f"устарели данные {format_age(age)}", age * 1000
    err = (t.get("last_error") or "").strip()
    if err:
        return False, err, t.get("last_latency_ms")
    lat = t.get("last_latency_ms")
    return True, None, int(lat) if lat is not None else age * 1000


async def run_router_monitor_tick(
    db: Database,
    notify: NotifyFn,
    *,
    global_settings: dict[str, Any],
    global_quiet_down: bool,
    monitor_state: EntityMonitorState,
) -> None:
    routers = await queries.list_monitored_routers(db)
    for r in routers:
        if not r["enabled"]:
            continue
        rid = int(r["id"])
        rname = str(r["display_name"])
        eff = queries.effective_monitor_for_entity(global_settings, r)
        st = monitor_state.get("router", rid)

        age = _ts_age_sec(r.get("last_heartbeat_at"))
        if age is None:
            router_alive = False
            r_err = "нет ни одного heartbeat"
            r_lat: int | None = None
        else:
            router_alive = age <= eff.heartbeat_timeout_sec
            r_err = None if router_alive else f"нет heartbeat {format_age(age)}"
            r_lat = age * 1000

        r_slow = (
            router_alive
            and eff.slow_ms > 0
            and r_lat is not None
            and r_lat > eff.slow_ms
        )

        async def _get_open() -> dict[str, Any] | None:
            return await queries.get_open_router_incident(db, rid)

        await process_entity_tick(
            st,
            is_down=not router_alive,
            is_slow=r_slow,
            latency_ms=r_lat,
            err_text=r_err,
            label=f"роутер {rname} (id={rid})",
            eff=eff,
            global_quiet_down=global_quiet_down,
            notify=notify,
            get_open_incident=_get_open,
            open_incident=lambda err: queries.open_router_incident(db, rid, err),
            close_incident=lambda iid: queries.close_router_incident(db, iid),
            update_incident_error=lambda iid, err: queries.update_router_incident_error(
                db, iid, err
            ),
            touch_incident_alert=lambda iid: queries.touch_router_incident_alert(db, iid),
            down_prefix="Роутер недоступен",
            down_repeat_prefix="Роутер всё ещё недоступен",
            recover_suffix="Heartbeat снова приходит.",
        )

        if not router_alive:
            continue

        targets = await queries.list_router_targets(db, rid, enabled_only=True)
        for t in targets:
            tid = int(t["id"])
            tname = str(t["display_name"])
            addr = str(t["address"])
            label = f"{rname} / {tname} ({addr})"
            teff = queries.effective_monitor_for_entity(global_settings, t)
            tst = monitor_state.get("target", tid)

            alive, err_text, lat_ms = _target_alive(t, teff.heartbeat_timeout_sec)
            is_slow = (
                alive
                and teff.slow_ms > 0
                and lat_ms is not None
                and lat_ms > teff.slow_ms
            )

            await queries.insert_router_target_check(
                db,
                tid,
                alive,
                t.get("last_latency_ms"),
                err_text,
                check_type="heartbeat_eval",
            )

            async def _get_t_open(tid: int = tid) -> dict[str, Any] | None:
                return await queries.get_open_router_target_incident(db, tid)

            await process_entity_tick(
                tst,
                is_down=not alive,
                is_slow=is_slow,
                latency_ms=lat_ms,
                err_text=err_text,
                label=label,
                eff=teff,
                global_quiet_down=global_quiet_down,
                notify=notify,
                get_open_incident=_get_t_open,
                open_incident=lambda err, tid=tid: queries.open_router_target_incident(
                    db, tid, err
                ),
                close_incident=lambda iid: queries.close_router_target_incident(db, iid),
                update_incident_error=lambda iid, err: queries.update_router_target_incident_error(
                    db, iid, err
                ),
                touch_incident_alert=lambda iid: queries.touch_router_target_incident_alert(
                    db, iid
                ),
            )
