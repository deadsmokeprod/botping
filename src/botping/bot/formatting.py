from __future__ import annotations

import os
from datetime import datetime, timedelta

from botping.db import queries
from botping.db.pool import Database
from botping.heartbeat_server import HeartbeatServer
from botping.monitor.disk_guard import get_disk_info
from botping.monitor.router_monitor import _target_alive
from botping.monitor.website_monitor import _module_alive
from botping.router_events import internet_channel_label
from botping.timeutil import MOSCOW_TZ, now_moscow_naive

from botping.bot.common import effective_hb_timeout_sec, format_age_ru, heartbeat_age_sec


def mask_token(token: str) -> str:
    t = token.strip()
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}…{t[-4:]}"


def mask_secret(secret: str) -> str:
    s = (secret or "").strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}…{s[-4:]}"


def public_host_from_env() -> str:
    url = os.getenv("BOTPING_PUBLIC_URL", "").strip().rstrip("/")
    if url:
        return url
    host = os.getenv("BOTPING_PUBLIC_HOST", "").strip()
    port = os.getenv("HEARTBEAT_PORT", "8080").strip() or "8080"
    if host:
        return f"http://{host}:{port}"
    return "http://<IP_VPS>:{port}"


def _telegram_api_diagnosis(stats: dict) -> str:
    if not stats.get("total"):
        return ""
    parts: list[str] = []
    failed = int(stats.get("failed") or 0)
    if failed:
        parts.append(f"за 24ч {failed} сбоев")
    rl = int(stats.get("rate_limited") or 0)
    if rl:
        parts.append(f"429×{rl} — лимит Telegram")
    avg_fail = stats.get("avg_fail_ms")
    avg_ok = stats.get("avg_ok_ms")
    if failed and not rl and avg_fail is not None and avg_fail < 150:
        parts.append("быстрый отказ — сеть/VPS, не «лежит» Telegram")
    elif failed and avg_ok is not None and avg_ok > 0:
        parts.append(f"при успехе ~{avg_ok}ms")
    if not parts:
        return ""
    return "\n   ↳ " + "; ".join(parts)


def heartbeat_snippet(secret: str) -> str:
    base = public_host_from_env()
    return (
        "# --- Botping heartbeat (вставьте рядом с dp.start_polling) ---\n"
        "import asyncio, httpx\n"
        f"BOTPING_URL = \"{base}/heartbeat\"\n"
        f"HEARTBEAT_SECRET = \"{secret}\"\n"
        "\n"
        "async def _botping_heartbeat():\n"
        "    headers = {\"X-Heartbeat-Secret\": HEARTBEAT_SECRET}\n"
        "    async with httpx.AsyncClient(timeout=10) as client:\n"
        "        while True:\n"
        "            try:\n"
        "                await client.post(BOTPING_URL, headers=headers)\n"
        "            except Exception:\n"
        "                pass\n"
        "            await asyncio.sleep(30)\n"
        "\n"
        "# asyncio.create_task(_botping_heartbeat())\n"
    )


async def format_status(db: Database, hb_server: HeartbeatServer | None = None) -> str:
    bots = await queries.list_monitored_bots(db)
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    tg_last = await queries.get_last_telegram_check(db)
    tg_inc = await queries.get_open_telegram_incident(db)
    tg_stats = await queries.get_telegram_check_stats_24h(db)

    lines: list[str] = ["📊 <b>Статус</b>", ""]

    if not bots:
        lines.append("📡 Боты: нет. Добавьте через 🤖 Боты → ➕")
    else:
        lines.append("📡 <b>Боты</b>")
        for b in bots:
            inc = await queries.get_open_incident(db, int(b["id"]))
            st = "✅ вкл" if b["enabled"] else "⏸ выкл"
            age = heartbeat_age_sec(b)
            b_hb = effective_hb_timeout_sec(settings, b)
            inc_s = " 🚨 ИНЦИДЕНТ" if inc else ""
            if age is None:
                state = "⚪ нет пингов"
            elif age <= b_hb:
                state = f"🟢 жив, {_fmt_age(age)} назад"
            else:
                state = f"🔴 недоступен, {_fmt_age(age)} без пинга"
            lines.append(f"• {b['display_name']} ({st}): {state}{inc_s}")

    lines.append("")
    lines.append(
        f"⏱ Порог пинга (глобально): {_fmt_age(hb_timeout)} — у объектов со ✎ свои значения"
    )

    if settings.get("telegram_api_probe_enabled", True):
        if not tg_last:
            tg_line = "☁️ Telegram API: проверок не было"
        else:
            tg_ok = tg_last["ok"]
            emoji = "🟢" if tg_ok else "🔴"
            tg_lat = tg_last["latency_ms"] if tg_last["latency_ms"] is not None else "?"
            tg_rl = " ⚠️ rate_limit" if tg_last.get("rate_limited") else ""
            tg_err = f" — {tg_last['error_text']}" if tg_last["error_text"] else ""
            tg_inc_s = " 🚨" if tg_inc else ""
            tg_diag = _telegram_api_diagnosis(tg_stats)
            tg_line = (
                f"☁️ Telegram API: {emoji} {tg_last['ts']}, {tg_lat}ms"
                f"{tg_rl}{tg_err}{tg_inc_s}{tg_diag}"
            )
        lines.append(tg_line)
    else:
        lines.append("☁️ Telegram API: ⏸ отключена")

    if hb_server is not None:
        try:
            st = hb_server.stats_snapshot()
            lines.append(
                f"🛡 Heartbeat: банов {st['active_bans']}, "
                f"за сутки {st['blocked_24h']}"
            )
        except Exception:
            lines.append("🛡 Heartbeat: н/д")

    info = get_disk_info(db.path)
    ck_stats = await queries.get_checks_storage_stats(db)
    routers = await queries.list_monitored_routers(db)
    lines.append("")
    lines.append("🌐 <b>Роутеры</b>")
    if not routers:
        lines.append("Нет роутеров — 🌐 Роутеры → ➕")
    else:
        for r in routers:
            rid = int(r["id"])
            st = "✅" if r["enabled"] else "⏸"
            age = heartbeat_age_sec(r)
            r_hb = effective_hb_timeout_sec(settings, r)
            r_inc = await queries.get_open_router_incident(db, rid)
            inc_s = " 🚨" if r_inc else ""
            if age is None:
                r_state = "⚪ нет пингов"
            elif age <= r_hb:
                r_state = f"🟢 {_fmt_age(age)} назад"
            else:
                r_state = f"🔴 {_fmt_age(age)} без пинга"
            last_ev = await queries.get_latest_router_event(db, rid)
            ch = (
                internet_channel_label(str(last_ev["event_type"]))
                if last_ev
                else None
            )
            ch_s = f", 📶 {ch}" if ch else ""
            lines.append(f"• {st} {r['display_name']}: {r_state}{ch_s}{inc_s}")
            if not r["enabled"]:
                continue
            targets = await queries.list_router_targets(db, rid, enabled_only=True)
            for t in targets:
                tid = int(t["id"])
                t_inc = await queries.get_open_router_target_incident(db, tid)
                t_inc_s = " 🚨" if t_inc else ""
                t_hb = effective_hb_timeout_sec(settings, t)
                alive, terr, _ = _target_alive(t, t_hb)
                if alive:
                    ms = t.get("last_latency_ms")
                    t_st = f"🟢 {ms} ms" if ms is not None else "🟢 жив"
                else:
                    t_st = f"🔴 {terr or '?'}"
                lines.append(f"  · {t['display_name']} {t['address']}: {t_st}{t_inc_s}")

    websites = await queries.list_monitored_websites(db)
    lines.append("")
    lines.append("🌍 <b>Сайты</b>")
    if not websites:
        lines.append("Нет сайтов — 🌍 Сайты → ➕")
    else:
        for w in websites:
            wid = int(w["id"])
            st = "✅" if w["enabled"] else "⏸"
            age = heartbeat_age_sec(w)
            w_hb = effective_hb_timeout_sec(settings, w)
            w_inc = await queries.get_open_website_incident(db, wid)
            inc_s = " 🚨" if w_inc else ""
            if age is None:
                w_state = "⚪ нет пингов"
            elif age <= w_hb:
                w_state = f"🟢 {_fmt_age(age)} назад"
            else:
                w_state = f"🔴 {_fmt_age(age)} без пинга"
            ip = w.get("last_resolved_ip") or "—"
            lines.append(
                f"• {st} {w['display_name']} ({w['host']}, IP {ip}): {w_state}{inc_s}"
            )
            if not w["enabled"]:
                continue
            modules = await queries.list_website_modules(db, wid, enabled_only=True)
            for m in modules:
                mid = int(m["id"])
                m_inc = await queries.get_open_website_module_incident(db, mid)
                m_inc_s = " 🚨" if m_inc else ""
                m_hb = effective_hb_timeout_sec(settings, m)
                alive, merr, _ = _module_alive(m, m_hb)
                if alive:
                    ms = m.get("last_latency_ms")
                    m_st = f"🟢 {ms} ms" if ms is not None else "🟢 жив"
                else:
                    m_st = f"🔴 {merr or '?'}"
                lines.append(f"  · {m['display_name']}: {m_st}{m_inc_s}")

    lines.append("")
    lines.append(
        f"💾 Диск: {info.used_pct:.0f}% ({info.used_gb:.1f}/{info.total_gb:.1f} ГБ), "
        f"БД {info.db_size_mb:.1f} МБ, проверок {ck_stats['count']}"
    )
    return "\n".join(lines)


def _fmt_age(sec: int) -> str:
    return format_age_ru(sec)


async def format_disk_detail(db: Database) -> str:
    info = get_disk_info(db.path)
    ck_stats = await queries.get_checks_storage_stats(db)
    settings = await queries.load_all_settings(db)
    threshold = settings["disk_usage_threshold_pct"]
    return (
        "💾 <b>Дисковое пространство</b>\n\n"
        f"📦 Всего: {info.total_gb:.1f} ГБ\n"
        f"📊 Занято: {info.used_gb:.1f} ГБ ({info.used_pct:.1f}%)\n"
        f"✨ Свободно: {info.free_gb:.1f} ГБ\n"
        f"⚠️ Порог очистки: {threshold}%\n\n"
        f"🗄 База: {info.db_size_mb:.1f} МБ\n"
        f"📝 Проверок: {ck_stats['count']}\n"
        f"🕐 Старейшая: {ck_stats['min_ts'] or '—'}\n"
        f"🕑 Новейшая: {ck_stats['max_ts'] or '—'}"
    )


async def format_failures_text(db: Database, args: str | None) -> str:
    end = now_moscow_naive().replace(microsecond=0)
    start = (now_moscow_naive() - timedelta(days=7)).replace(microsecond=0)
    if args:
        parts = args.split()
        if len(parts) >= 2:
            try:
                d0 = datetime.strptime(parts[0], "%Y-%m-%d")
                d1 = datetime.strptime(parts[1], "%Y-%m-%d")
                start = d0
                end = d1.replace(hour=23, minute=59, second=59)
            except ValueError:
                return "⚠️ Формат: /failures или /failures 2026-04-01 2026-04-14"
    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")
    items = await queries.list_all_incidents_in_range(db, start_s, end_s)
    if not items:
        return f"⚠️ <b>Сбои</b>\n\nЗа {start_s} — {end_s} инцидентов нет."
    lines = [f"⚠️ <b>Инциденты</b> ({start_s} — {end_s})", ""]
    for it in items:
        ended = it["ended_at"] or "🔴 открыт"
        et = it.get("entity_type", "bot")
        eid = it.get("entity_id", "")
        lines.append(
            f"• [{et}] {it['display_name']} (id={eid})\n"
            f"  {it['started_at']} → {ended}\n"
            f"  {it['last_error']}"
        )
    return "\n".join(lines)


def chunk_text(s: str, limit: int = 3900) -> list[str]:
    if len(s) <= limit:
        return [s]
    parts: list[str] = []
    cur = ""
    for line in s.splitlines():
        if len(cur) + len(line) + 1 > limit:
            if cur:
                parts.append(cur)
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur:
        parts.append(cur)
    return parts
