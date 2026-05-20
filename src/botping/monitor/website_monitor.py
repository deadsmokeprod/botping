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


def _module_alive(
    m: dict, hb_timeout: int
) -> tuple[bool, str | None, int | None]:
    age = _ts_age_sec(m.get("last_ok_at"))
    if age is None:
        return False, "нет данных от агента", None
    if age > hb_timeout:
        return False, f"устарели данные {format_age(age)}", age * 1000
    err = (m.get("last_error") or "").strip()
    if err:
        return False, err, m.get("last_latency_ms")
    lat = m.get("last_latency_ms")
    return True, None, int(lat) if lat is not None else age * 1000


async def run_website_monitor_tick(
    db: Database,
    notify: NotifyFn,
    *,
    global_settings: dict[str, Any],
    global_quiet_down: bool,
    monitor_state: EntityMonitorState,
) -> None:
    websites = await queries.list_monitored_websites(db)
    for w in websites:
        if not w["enabled"]:
            continue
        wid = int(w["id"])
        wname = str(w["display_name"])
        host = str(w["host"])
        label_base = f"{wname} ({host})"
        eff = queries.effective_monitor_for_entity(global_settings, w)
        st = monitor_state.get("website", wid)

        age = _ts_age_sec(w.get("last_heartbeat_at"))
        if age is None:
            website_alive = False
            w_err = "нет ни одного heartbeat"
            w_lat: int | None = None
        else:
            website_alive = age <= eff.heartbeat_timeout_sec
            w_err = None if website_alive else f"нет heartbeat {format_age(age)}"
            w_lat = age * 1000

        w_slow = (
            website_alive
            and eff.slow_ms > 0
            and w_lat is not None
            and w_lat > eff.slow_ms
        )

        async def _get_open() -> dict[str, Any] | None:
            return await queries.get_open_website_incident(db, wid)

        await process_entity_tick(
            st,
            is_down=not website_alive,
            is_slow=w_slow,
            latency_ms=w_lat,
            err_text=w_err,
            label=f"сайт {label_base} (id={wid})",
            eff=eff,
            global_quiet_down=global_quiet_down,
            notify=notify,
            get_open_incident=_get_open,
            open_incident=lambda err: queries.open_website_incident(db, wid, err),
            close_incident=lambda iid: queries.close_website_incident(db, iid),
            update_incident_error=lambda iid, err: queries.update_website_incident_error(
                db, iid, err
            ),
            touch_incident_alert=lambda iid: queries.touch_website_incident_alert(db, iid),
            down_prefix="Сайт недоступен",
            down_repeat_prefix="Сайт всё ещё недоступен",
            recover_suffix="Heartbeat снова приходит.",
        )

        if not website_alive:
            continue

        modules = await queries.list_website_modules(db, wid, enabled_only=True)
        for m in modules:
            mid = int(m["id"])
            mname = str(m["display_name"])
            label = f"{label_base} / {mname}"
            meff = queries.effective_monitor_for_entity(global_settings, m, parent_row=w)
            mst = monitor_state.get("module", mid)

            alive, err_text, lat_ms = _module_alive(m, meff.heartbeat_timeout_sec)
            is_slow = (
                alive
                and meff.slow_ms > 0
                and lat_ms is not None
                and lat_ms > meff.slow_ms
            )

            await queries.insert_website_module_check(
                db,
                mid,
                alive,
                m.get("last_latency_ms"),
                err_text,
                check_type="heartbeat_eval",
            )

            async def _get_m_open(mid: int = mid) -> dict[str, Any] | None:
                return await queries.get_open_website_module_incident(db, mid)

            await process_entity_tick(
                mst,
                is_down=not alive,
                is_slow=is_slow,
                latency_ms=lat_ms,
                err_text=err_text,
                label=label,
                eff=meff,
                global_quiet_down=global_quiet_down,
                notify=notify,
                get_open_incident=_get_m_open,
                open_incident=lambda err, mid=mid: queries.open_website_module_incident(
                    db, mid, err
                ),
                close_incident=lambda iid: queries.close_website_module_incident(db, iid),
                update_incident_error=lambda iid, err: queries.update_website_module_incident_error(
                    db, iid, err
                ),
                touch_incident_alert=lambda iid: queries.touch_website_module_incident_alert(
                    db, iid
                ),
            )
