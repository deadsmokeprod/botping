from __future__ import annotations

from botping.bot.common import effective_hb_timeout_sec, format_age_ru, heartbeat_age_sec, mask_secret
from botping.db import queries
from botping.db.pool import Database
from botping.monitor.website_monitor import _module_alive

WEBSITES_MENU_INTRO = (
    "🌍 <b>Сайты</b>\n\n"
    "1️⃣ Добавьте сайт (домен)\n"
    "2️⃣ Модули проверки (форма, API…)\n"
    "3️⃣ 🔧 Установка агента на сервере сайта\n"
    "4️⃣ 📊 Статус — всё «живо»"
)


def module_status_line(m: dict, hb_timeout: int, slow_ms: int = 0) -> str:
    alive, err, lat = _module_alive(m, hb_timeout)
    if alive:
        ms = m.get("last_latency_ms") if m.get("last_latency_ms") is not None else lat
        if slow_ms > 0 and ms is not None and int(ms) > slow_ms:
            return f"🐢 медленно, {ms} ms"
        return f"🟢 жив{f', {ms} ms' if ms is not None else ''}"
    return f"🔴 нет ({err or '?'})"


def module_button_label(m: dict, hb_timeout: int) -> str:
    st = module_status_line(m, hb_timeout)
    name = str(m["display_name"])[:20]
    return f"{st} · {name}"


def site_check_line(w: dict, hb_timeout: int) -> str:
    from botping.monitor.util import parse_sqlite_ts
    from botping.timeutil import MOSCOW_TZ
    from datetime import datetime

    ts = w.get("last_site_ok_at")
    if not ts:
        return "⚪ сайт: нет данных"
    last = parse_sqlite_ts(str(ts))
    if last is None:
        return "⚪ сайт: нет данных"
    age = max(0, int((datetime.now(MOSCOW_TZ) - last).total_seconds()))
    err = (w.get("last_site_error") or "").strip()
    if age > hb_timeout:
        return f"🔴 сайт: устарело {format_age_ru(age)}"
    if err:
        return f"🔴 сайт: {err}"
    ms = w.get("last_site_latency_ms")
    return f"🟢 сайт{f', {ms} ms' if ms is not None else ''}"


async def website_summary_label(
    db: Database, w: dict, global_settings: dict
) -> str:
    wid = int(w["id"])
    w_hb = effective_hb_timeout_sec(global_settings, w)
    age = heartbeat_age_sec(w)
    if age is None:
        w_st = "⚪"
    elif age <= w_hb:
        w_st = "🟢"
    else:
        w_st = "🔴"
    if not w["enabled"]:
        w_st = f"{w_st}⏸"
    modules = await queries.list_website_modules(db, wid, enabled_only=True)
    if not modules:
        ok_s = "0 мод."
    else:
        ok_n = sum(
            1
            for m in modules
            if _module_alive(m, effective_hb_timeout_sec(global_settings, m, parent_row=w))[0]
        )
        ok_s = f"{ok_n}/{len(modules)}"
    name = str(w["display_name"])[:24]
    host = str(w["host"])[:20]
    return f"{w_st} {name} · {host} · {ok_s}"


async def build_website_menu_rows(db: Database) -> list[tuple[int, str]]:
    settings = await queries.load_all_settings(db)
    rows = await queries.list_monitored_websites(db)
    out: list[tuple[int, str]] = []
    for w in rows:
        out.append((int(w["id"]), await website_summary_label(db, w, settings)))
    return out


async def format_website_detail(db: Database, website_id: int) -> str | None:
    w = await queries.get_monitored_website(db, website_id)
    if not w:
        return None
    settings = await queries.load_all_settings(db)
    w_hb = effective_hb_timeout_sec(settings, w)
    age = heartbeat_age_sec(w)
    if age is None:
        hb_state = "⚪ нет heartbeat"
    elif age <= w_hb:
        hb_state = f"🟢 жив, {format_age_ru(age)} назад"
    else:
        hb_state = f"🔴 недоступен, {format_age_ru(age)} без пинга"
    en = "✅ включён" if w["enabled"] else "⏸ выключен"
    lines = [
        f"🌍 <b>{w['display_name']}</b>",
        f"id={website_id} · {en}",
        f"💓 {hb_state}",
        f"🔗 Домен: <code>{w['host']}</code>",
        f"🌐 IP: {w.get('last_resolved_ip') or '—'}",
        site_check_line(w, w_hb),
        f"⏱ Таймаут: {w_hb} с"
        + (" (свои)" if queries.count_override_keys(w.get("settings_override")) else " (общие)"),
        f"🕑 Пинг: {w.get('last_heartbeat_at') or '—'}",
        f"📡 Агент: {w.get('last_heartbeat_ip') or '—'}",
        f"🔑 Секрет: {mask_secret(str(w['heartbeat_secret']))}",
        "",
        "<b>Модули:</b>",
    ]
    modules = await queries.list_website_modules(db, website_id)
    if not modules:
        lines.append("— нет (➕ Модуль)")
    else:
        for m in modules:
            mid = int(m["id"])
            inc = await queries.get_open_website_module_incident(db, mid)
            inc_s = " 🚨" if inc else ""
            m_hb = effective_hb_timeout_sec(settings, m, parent_row=w)
            st = module_status_line(m, m_hb)
            hint = m.get("check_hint")
            hint_s = f" · {hint}" if hint else ""
            lines.append(f"• {st} <b>{m['display_name']}</b>{hint_s}{inc_s}")
    return "\n".join(lines)


async def module_buttons_for_website(
    db: Database, website_id: int, global_settings: dict, website_row: dict
) -> list[tuple[int, str]]:
    modules = await queries.list_website_modules(db, website_id)
    return [
        (
            int(m["id"]),
            module_button_label(
                m, effective_hb_timeout_sec(global_settings, m, parent_row=website_row)
            ),
        )
        for m in modules
    ]
