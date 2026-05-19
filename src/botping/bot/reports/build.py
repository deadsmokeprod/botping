from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from botping.bot.reports.excel_report import build_availability_report
from botping.db import queries
from botping.db.pool import Database
from botping.timeutil import now_moscow_naive


@dataclass(frozen=True)
class AvailabilityReportBundle:
    blob: bytes
    filename: str
    caption: str


async def build_availability_report_bundle(
    db: Database,
    period_start: datetime,
    period_end: datetime,
) -> AvailabilityReportBundle:
    start_iso = period_start.strftime("%Y-%m-%d %H:%M:%S")
    end_iso = period_end.strftime("%Y-%m-%d %H:%M:%S")
    bots = await queries.list_monitored_bots(db)
    checks, c_trunc = await queries.export_checks_for_report(db, start_iso, end_iso)
    incidents, i_trunc = await queries.export_incidents_overlapping(db, start_iso, end_iso)
    audit, a_trunc = await queries.export_settings_audit_for_report(db, start_iso, end_iso)
    tg_checks, tc_trunc = await queries.export_telegram_checks_for_report(db, start_iso, end_iso)
    tg_incidents, ti_trunc = await queries.export_telegram_incidents_overlapping(db, start_iso, end_iso)
    routers = await queries.list_monitored_routers(db)
    rt_checks, rtc_trunc = await queries.export_router_target_checks_for_report(
        db, start_iso, end_iso
    )
    r_incidents, ri_trunc = await queries.export_router_incidents_overlapping(
        db, start_iso, end_iso
    )
    rt_incidents, rti_trunc = await queries.export_router_target_incidents_overlapping(
        db, start_iso, end_iso
    )
    websites = await queries.list_monitored_websites(db)
    wm_checks, wmc_trunc = await queries.export_website_module_checks_for_report(
        db, start_iso, end_iso
    )
    w_incidents, wi_trunc = await queries.export_website_incidents_overlapping(
        db, start_iso, end_iso
    )
    wm_incidents, wmi_trunc = await queries.export_website_module_incidents_overlapping(
        db, start_iso, end_iso
    )
    settings_rows = await queries.list_settings_raw_pairs_for_report(db)
    ck_stats = await queries.get_checks_storage_stats(db)
    now = now_moscow_naive()
    blob = build_availability_report(
        period_start=period_start,
        period_end=period_end,
        generated_at=now,
        bots=bots,
        checks=checks,
        incidents=incidents,
        audit=audit,
        settings_rows=settings_rows,
        checks_truncated=c_trunc,
        incidents_truncated=i_trunc,
        audit_truncated=a_trunc,
        checks_total_in_db=int(ck_stats["count"]),
        checks_db_min_ts=ck_stats.get("min_ts"),
        checks_db_max_ts=ck_stats.get("max_ts"),
        telegram_checks=tg_checks,
        telegram_incidents=tg_incidents,
        telegram_checks_truncated=tc_trunc,
        telegram_incidents_truncated=ti_trunc,
        routers=routers,
        router_target_checks=rt_checks,
        router_incidents=r_incidents,
        router_target_incidents=rt_incidents,
        router_checks_truncated=rtc_trunc,
        router_incidents_truncated=ri_trunc,
        router_target_incidents_truncated=rti_trunc,
        websites=websites,
        website_module_checks=wm_checks,
        website_incidents=w_incidents,
        website_module_incidents=wm_incidents,
        website_checks_truncated=wmc_trunc,
        website_incidents_truncated=wi_trunc,
        website_module_incidents_truncated=wmi_trunc,
    )
    fn = f"botping_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}.xlsx"
    cap = (
        f"Период: {period_start.strftime('%d.%m.%Y')} — {period_end.strftime('%d.%m.%Y')}. "
        f"Проверок ботов: {len(checks)}, LAN: {len(rt_checks)}, модулей: {len(wm_checks)}, "
        f"инцидентов: {len(incidents) + len(r_incidents) + len(rt_incidents) + len(w_incidents) + len(wm_incidents)}."
    )
    if c_trunc or i_trunc or a_trunc:
        cap += " Часть строк обрезана по лимиту экспорта — см. лист «Сводка»."
    return AvailabilityReportBundle(blob=blob, filename=fn, caption=cap[:1024])
