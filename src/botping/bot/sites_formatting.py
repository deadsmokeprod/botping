from __future__ import annotations

from botping.bot.common import format_age_ru, heartbeat_age_sec, mask_secret, public_host_from_env
from botping.db import queries
from botping.db.pool import Database
from botping.monitor.router_monitor import _target_alive
from botping.router_events import internet_channel_label

ROUTERS_MENU_INTRO = (
    "🌐 <b>Роутеры и устройства</b> (MikroTik)\n\n"
    "1️⃣ Добавьте роутер\n"
    "2️⃣ Устройства в LAN (IP)\n"
    "3️⃣ 🔧 Установка на MikroTik\n"
    "4️⃣ 📶 WAN/LTE уведомления\n"
    "5️⃣ 📊 Статус — всё «живо»"
)


def target_status_line(t: dict, hb_timeout: int) -> str:
    alive, err, _ = _target_alive(t, hb_timeout)
    if alive:
        ms = t.get("last_latency_ms")
        return f"🟢 жив{f', {ms} ms' if ms is not None else ''}"
    return f"🔴 нет ({err or '?'})"


def target_button_label(t: dict, hb_timeout: int) -> str:
    st = target_status_line(t, hb_timeout)
    name = str(t["display_name"])[:20]
    addr = str(t["address"])
    return f"{st} · {name} · {addr}"


async def router_summary_label(db: Database, r: dict, hb_timeout: int) -> str:
    rid = int(r["id"])
    age = heartbeat_age_sec(r)
    if age is None:
        r_st = "⚪"
    elif age <= hb_timeout:
        r_st = "🟢"
    else:
        r_st = "🔴"
    if not r["enabled"]:
        r_st = f"{r_st}⏸"
    targets = await queries.list_router_targets(db, rid, enabled_only=True)
    if not targets:
        ok_s = "0 целей"
    else:
        ok_n = sum(1 for t in targets if _target_alive(t, hb_timeout)[0])
        ok_s = f"{ok_n}/{len(targets)}"
    name = str(r["display_name"])[:28]
    return f"{r_st} {name} · {ok_s}"


async def build_router_menu_rows(db: Database) -> list[tuple[int, str]]:
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    rows = await queries.list_monitored_routers(db)
    out: list[tuple[int, str]] = []
    for r in rows:
        out.append((int(r["id"]), await router_summary_label(db, r, hb_timeout)))
    return out


async def format_site_detail(db: Database, router_id: int) -> str | None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return None
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    age = heartbeat_age_sec(r)
    if age is None:
        hb_state = "⚪ нет heartbeat"
    elif age <= hb_timeout:
        hb_state = f"🟢 жив, {format_age_ru(age)} назад"
    else:
        hb_state = f"🔴 недоступен, {format_age_ru(age)} без пинга"
    last_ev = await queries.get_latest_router_event(db, router_id)
    if last_ev:
        ch = internet_channel_label(str(last_ev["event_type"]))
        channel_line = f"📶 Канал: {ch}" if ch else "📶 Канал: (см. журнал)"
        last_sw = f"🕐 Переключение: {last_ev['created_at']}"
    else:
        channel_line = "📶 Канал: ещё не было"
        last_sw = "🕐 Переключение: —"
    en = "✅ включён" if r["enabled"] else "⏸ выключен"
    lines = [
        f"🌐 <b>{r['display_name']}</b>",
        f"id={router_id} · {en}",
        f"💓 {hb_state}",
        channel_line,
        last_sw,
        f"🕑 Пинг: {r.get('last_heartbeat_at') or '—'}",
        f"🌍 IP: {r.get('last_heartbeat_ip') or '—'}",
        f"🔑 Секрет: {mask_secret(str(r['heartbeat_secret']))}",
        "",
        "<b>Устройства в LAN:</b>",
    ]
    targets = await queries.list_router_targets(db, router_id)
    if not targets:
        lines.append("  (нет — ➕ Устройство в LAN)")
    else:
        for t in targets:
            tid = int(t["id"])
            inc = await queries.get_open_router_target_incident(db, tid)
            inc_s = " 🚨" if inc else ""
            st = "✅" if t["enabled"] else "⏸"
            lines.append(
                f"  · [{tid}] {st} {t['display_name']} {t['address']}: "
                f"{target_status_line(t, hb_timeout)}{inc_s}"
            )
    return "\n".join(lines)


async def target_buttons_for_router(
    db: Database, router_id: int, hb_timeout: int
) -> list[tuple[int, str]]:
    targets = await queries.list_router_targets(db, router_id)
    return [(int(t["id"]), target_button_label(t, hb_timeout)) for t in targets]


async def format_event_log(db: Database, router_id: int) -> str | None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return None
    events = await queries.list_router_events(db, router_id, limit=20)
    lines = [f"📜 <b>Журнал</b> — {r['display_name']}", ""]
    if not events:
        lines.append("Записей нет. Настройте 📶 WAN/LTE.")
    else:
        for ev in events:
            lines.append(f"• {ev['created_at']}")
            lines.append(f"  {ev['message']}")
    return "\n".join(lines)
