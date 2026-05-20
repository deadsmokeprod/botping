from __future__ import annotations

from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from botping.bot import copy as ui
from botping.bot import keyboards as kb
from botping.bot.formatting import (
    format_disk_detail,
    format_failures_text,
    format_status,
    mask_secret,
    mask_token,
    public_host_from_env,
)
from botping.bot.settings_help import (
    ENTITY_KIND_LABELS,
    META,
    SETTINGS_GROUPS,
    format_entity_key_change_prompt,
    format_key_change_prompt,
)
from botping.bot.sites_formatting import (
    ROUTERS_MENU_INTRO,
    build_router_menu_rows,
    format_site_detail,
    target_buttons_for_router,
    target_status_line,
)
from botping.bot.websites_formatting import (
    WEBSITES_MENU_INTRO,
    build_website_menu_rows,
    format_website_detail,
    module_buttons_for_website,
    module_status_line,
)
from botping.db import queries
from botping.db.pool import Database
from botping.heartbeat_server import HeartbeatServer

from botping.bot.common import heartbeat_age_sec, format_age_ru


def _report_period_prompt_text() -> str:
    return (
        "📈 <b>Отчёт Excel</b>\n\n"
        "Отправьте период «от — до» одной строкой:\n"
        "• 15.04.2026 по 20.04.2026\n"
        "• 15042025,20042025 (ДДММГГГГ)\n\n"
        "Даты по Москве (UTC+3)."
    )


async def render_screen(
    screen_key: str,
    db: Database,
    *,
    hb_server: HeartbeatServer | None = None,
    state: FSMContext | None = None,
    failures_args: str | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    if screen_key == "main":
        return (
            f"{ui.MAIN_MENU_TITLE}\n\n{ui.MAIN_MENU_BODY}",
            kb.main_menu(),
        )
    if screen_key == "help":
        return ui.HELP_TEXT, kb.help_keyboard()
    if screen_key == "settings":
        return ui.SETTINGS_INTRO, kb.settings_menu()
    if screen_key.startswith("setgrp:"):
        gid = screen_key.split(":", 1)[1]
        g = SETTINGS_GROUPS.get(gid, {"title": gid, "emoji": "⚙️"})
        text = (
            f"{g.get('emoji', '⚙️')} <b>{g.get('title', gid)}</b>\n\n"
            "Выберите параметр. Значения по умолчанию для всех объектов "
            "без своих настроек."
        )
        return text, kb.settings_group_menu(gid)
    if screen_key == "bots":
        rows = await queries.list_monitored_bots(db)
        bot_rows = [(int(b["id"]), str(b["display_name"]), bool(b["enabled"])) for b in rows]
        text = ui.BOTS_LIST_TITLE
        if not bot_rows:
            text += "\n\nПока нет ботов — нажмите ➕"
        return text, kb.bots_menu(bot_rows)
    if screen_key == "routers":
        menu_rows = await build_router_menu_rows(db)
        text = ROUTERS_MENU_INTRO
        if not menu_rows:
            text += "\n\nПока нет роутеров — ➕ Добавить"
        return text, kb.routers_menu(menu_rows)
    if screen_key == "websites":
        menu_rows = await build_website_menu_rows(db)
        text = WEBSITES_MENU_INTRO
        if not menu_rows:
            text += "\n\nПока нет сайтов — ➕ Добавить"
        return text, kb.websites_menu(menu_rows)
    if screen_key == "status":
        return await format_status(db, hb_server), kb.screen_with_back(kb.main_menu())
    if screen_key == "disk":
        return await format_disk_detail(db), kb.screen_with_back(kb.main_menu())
    if screen_key == "failures":
        text = await format_failures_text(db, failures_args)
        if len(text) > 4090:
            text = text[:4080] + "\n\n… <i>обрезано</i>"
        return text, kb.screen_with_back(kb.main_menu())
    if screen_key == "report_prompt":
        return _report_period_prompt_text(), kb.report_cancel_keyboard()
    if screen_key.startswith("bot:"):
        bid = int(screen_key.split(":")[1])
        return await _render_bot_detail(db, bid)
    if screen_key.startswith("router:"):
        rid = int(screen_key.split(":")[1])
        return await _render_router_detail(db, rid)
    if screen_key.startswith("website:"):
        wid = int(screen_key.split(":")[1])
        return await _render_website_detail(db, wid)
    if screen_key.startswith("webmod:"):
        mid = int(screen_key.split(":")[1])
        return await _render_webmod_detail(db, mid)
    if screen_key.startswith("target:"):
        tid = int(screen_key.split(":")[1])
        return await _render_target_detail(db, tid)
    if screen_key.startswith("setting:"):
        key = screen_key.split(":", 1)[1]
        prompt = await format_key_change_prompt(db, key)
        if len(prompt) > 4000:
            prompt = prompt[:3990] + "…"
        return f"⚙️ <b>{META[key].get('title', key)}</b>\n\n{prompt}", kb.setting_input_keyboard()
    if screen_key.startswith("eset:"):
        return await _render_entity_settings_screen(db, screen_key)
    if screen_key.startswith("bot_del:"):
        bid = int(screen_key.split(":")[1])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            return "❌ Не найден", kb.back_to_main_keyboard()
        return (
            f"🗑 Удалить бота «{b['display_name']}» (id={bid})?",
            kb.confirm_delete(bid),
        )
    if screen_key.startswith("router_del:"):
        rid = int(screen_key.split(":")[1])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            return "❌ Не найден", kb.back_to_main_keyboard()
        return (
            f"🗑 Удалить роутер «{r['display_name']}» и все устройства?",
            kb.confirm_site_delete(rid),
        )
    if screen_key.startswith("website_del:"):
        wid = int(screen_key.split(":")[1])
        w = await queries.get_monitored_website(db, wid)
        if not w:
            return "❌ Не найден", kb.back_to_main_keyboard()
        return (
            f"🗑 Удалить сайт «{w['display_name']}» и все модули?",
            kb.confirm_website_delete(wid),
        )
    if screen_key.startswith("webmod_del:"):
        mid = int(screen_key.split(":")[1])
        m = await queries.get_website_module(db, mid)
        if not m:
            return "❌ Не найден", kb.back_to_main_keyboard()
        return (
            f"🗑 Удалить модуль «{m['display_name']}»?",
            kb.confirm_webmod_delete(mid),
        )
    if screen_key.startswith("target_del:"):
        tid = int(screen_key.split(":")[1])
        t = await queries.get_router_target(db, tid)
        if not t:
            return "❌ Не найден", kb.back_to_main_keyboard()
        return (
            f"🗑 Удалить «{t['display_name']}» ({t['address']})?",
            kb.confirm_target_delete(tid, int(t["router_id"])),
        )
    if screen_key == "add_bot_name":
        return "🤖 Введите <b>отображаемое имя</b> бота:", kb.cancel_input_keyboard()
    if screen_key == "add_bot_token":
        return (
            "🔑 Отправьте <b>токен</b> из @BotFather.\n"
            "<i>Сообщение с токеном можно удалить вручную.</i>",
            kb.cancel_input_keyboard(),
        )
    if screen_key == "add_router_name":
        return (
            "🌐 <b>Шаг 1/3</b>: имя роутера или площадки\n"
            "(например «Офис»).",
            kb.cancel_input_keyboard(),
        )
    if screen_key == "add_website_name":
        return (
            "🌍 <b>Шаг 1/2</b>: имя сайта\n"
            "(например «Mongol»).",
            kb.cancel_input_keyboard(),
        )
    if screen_key == "add_website_host":
        return (
            "🔗 <b>Домен</b> (без https://)\n"
            "Например: mongol.pro",
            kb.cancel_input_keyboard(),
        )
    if screen_key.startswith("add_webmod_name"):
        return (
            "📦 <b>Имя модуля</b>\n"
            "(например «Форма обратной связи»):",
            kb.cancel_input_keyboard(),
        )
    if screen_key.startswith("add_webmod_hint"):
        return (
            "🔗 <b>URL или путь</b> для GET-проверки в агенте\n"
            "(например /contact/ или https://mongol.pro/contact/).\n"
            "Отправьте <code>-</code> чтобы пропустить (настроите вручную в скрипте).",
            kb.cancel_input_keyboard(),
        )
    if screen_key.startswith("website_created:"):
        wid = int(screen_key.split(":")[1])
        return (
            f"✅ Сайт создан (id={wid}).\n\n"
            "2️⃣ Добавьте модули проверки.\n"
            "3️⃣ Установите агент на сервере.",
            kb.website_after_create(wid),
        )
    if screen_key.startswith("add_target_name"):
        return "📡 <b>Имя устройства</b> (например «Камера»):", kb.cancel_input_keyboard()
    if screen_key.startswith("add_target_addr"):
        return "📍 <b>IP или hostname</b> в LAN (192.168.88.10):", kb.cancel_input_keyboard()
    if screen_key.startswith("router_created:"):
        rid = int(screen_key.split(":")[1])
        return (
            f"✅ Роутер создан (id={rid}).\n\n"
            "2️⃣ Добавьте устройства в LAN.\n"
            "3️⃣ Установите скрипт на MikroTik.",
            kb.router_after_create(rid),
        )
    if screen_key == "names_menu":
        return ui.NAMES_MENU_INTRO, kb.names_category_menu()
    if screen_key.startswith("names_list:"):
        kind = screen_key.split(":", 1)[1]
        return await _render_names_list(db, kind)
    if screen_key.startswith("names_edit:"):
        parts = screen_key.split(":")
        if len(parts) >= 4:
            kind, eid = parts[2], int(parts[3])
            return await _render_names_edit(db, kind, eid)
    return f"{ui.MAIN_MENU_TITLE}\n\n{ui.MAIN_MENU_BODY}", kb.main_menu()


async def _build_names_entity_rows(db: Database, kind: str) -> list[tuple[int, str]]:
    if kind == "bot":
        rows = await queries.list_monitored_bots(db)
        return [(int(b["id"]), f"{b['display_name']} (id={b['id']})") for b in rows]
    if kind == "router":
        rows = await queries.list_monitored_routers(db)
        return [(int(r["id"]), f"{r['display_name']} (id={r['id']})") for r in rows]
    if kind == "website":
        rows = await queries.list_monitored_websites(db)
        return [(int(w["id"]), f"{w['display_name']} (id={w['id']})") for w in rows]
    if kind == "target":
        out: list[tuple[int, str]] = []
        for r in await queries.list_monitored_routers(db):
            for t in await queries.list_router_targets(db, int(r["id"])):
                label = f"{r['display_name']} · {t['display_name']} ({t['address']})"
                out.append((int(t["id"]), label))
        return out
    if kind == "module":
        out = []
        for w in await queries.list_monitored_websites(db):
            for m in await queries.list_website_modules(db, int(w["id"])):
                label = f"{w['display_name']} · {m['display_name']}"
                out.append((int(m["id"]), label))
        return out
    return []


async def get_entity_display_name(db: Database, kind: str, entity_id: int) -> str | None:
    if kind == "bot":
        row = await queries.get_monitored_bot(db, entity_id)
    elif kind == "router":
        row = await queries.get_monitored_router(db, entity_id)
    elif kind == "target":
        row = await queries.get_router_target(db, entity_id)
    elif kind == "website":
        row = await queries.get_monitored_website(db, entity_id)
    elif kind == "module":
        row = await queries.get_website_module(db, entity_id)
    else:
        return None
    if not row:
        return None
    return str(row["display_name"])


async def _render_names_list(db: Database, kind: str) -> tuple[str, InlineKeyboardMarkup]:
    title = ui.NAMES_KIND_TITLES.get(kind, kind)
    entity_rows = await _build_names_entity_rows(db, kind)
    text = f"{ui.NAMES_MENU_INTRO}\n\n<b>{title}</b>\nВыберите объект:"
    if not entity_rows:
        text += f"\n\n{ui.NAMES_KIND_EMPTY_HINT.get(kind, 'Список пуст.')}"
    return text, kb.names_entity_list(kind, entity_rows)


async def _render_names_edit(
    db: Database, kind: str, entity_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    current = await get_entity_display_name(db, kind, entity_id)
    if current is None:
        return "❌ Объект не найден", kb.names_category_menu()
    title = ui.NAMES_KIND_TITLES.get(kind, kind)
    text = (
        f"✏️ <b>Переименование</b> · {title}\n"
        f"id={entity_id}\n\n"
        f"Сейчас: <b>{current}</b>\n\n"
        "Введите новое наименование (1–64 символа).\n"
        "<i>Домен, IP и id проверок не меняются.</i>"
    )
    return text, kb.names_cancel_keyboard()


async def _render_bot_detail(db: Database, bid: int) -> tuple[str, InlineKeyboardMarkup]:
    b = await queries.get_monitored_bot(db, bid)
    if not b:
        return "❌ Бот не найден", kb.back_to_main_keyboard()
    settings = await queries.load_all_settings(db)
    eff = queries.effective_monitor_for_entity(settings, b)
    hb_timeout = eff.heartbeat_timeout_sec
    age = heartbeat_age_sec(b)
    if age is None:
        hb_state = "⚪ нет пингов"
    elif age <= hb_timeout:
        hb_state = f"🟢 жив, {format_age_ru(age)} назад"
    else:
        hb_state = f"🔴 недоступен, {format_age_ru(age)}"
    en = "✅ вкл" if b["enabled"] else "⏸ выкл"
    text = (
        f"🤖 <b>{b['display_name']}</b>\n"
        f"id={bid} · {en}\n"
        f"🔑 Токен: {mask_token(str(b['token']))}\n"
        f"💓 {hb_state}\n"
        f"🕑 Пинг: {b.get('last_heartbeat_at') or '—'}\n"
        f"🌍 IP: {b.get('last_heartbeat_ip') or '—'}\n"
        f"🔐 Секрет: {mask_secret(str(b['heartbeat_secret']))}\n"
        f"🔗 {public_host_from_env()}/heartbeat"
    )
    ov = queries.count_override_keys(b.get("settings_override"))
    settings_line = f"⚙️ Настройки: {'свои (' + str(ov) + ')' if ov else 'общие'}"
    text += f"\n{settings_line}"
    return text, kb.bot_detail(bid, bool(b["enabled"]), ov)


async def _render_router_detail(db: Database, rid: int) -> tuple[str, InlineKeyboardMarkup]:
    text = await format_site_detail(db, rid)
    if not text:
        return "❌ Роутер не найден", kb.back_to_main_keyboard()
    r = await queries.get_monitored_router(db, rid)
    assert r is not None
    settings = await queries.load_all_settings(db)
    target_btns = await target_buttons_for_router(db, rid, settings)
    ov = queries.count_override_keys(r.get("settings_override"))
    return text, kb.router_detail(rid, bool(r["enabled"]), target_btns, ov)


async def _render_website_detail(db: Database, wid: int) -> tuple[str, InlineKeyboardMarkup]:
    text = await format_website_detail(db, wid)
    if not text:
        return "❌ Сайт не найден", kb.back_to_main_keyboard()
    w = await queries.get_monitored_website(db, wid)
    assert w is not None
    settings = await queries.load_all_settings(db)
    mod_btns = await module_buttons_for_website(db, wid, settings, w)
    return text, kb.website_detail(wid, bool(w["enabled"]), mod_btns)


async def _render_webmod_detail(db: Database, mid: int) -> tuple[str, InlineKeyboardMarkup]:
    m = await queries.get_website_module(db, mid)
    if not m:
        return "❌ Не найдено", kb.back_to_main_keyboard()
    wid = int(m["website_id"])
    settings = await queries.load_all_settings(db)
    parent_w = await queries.get_monitored_website(db, wid)
    meff = queries.effective_monitor_for_entity(
        settings, m, parent_row=parent_w
    )
    en = "✅ вкл" if m["enabled"] else "⏸ выкл"
    hint = m.get("check_hint") or "—"
    ov = queries.count_override_keys(m.get("settings_override"))
    text = (
        f"📦 <b>{m['display_name']}</b>\n"
        f"id={mid} · website {wid} · {en}\n"
        f"🔗 Проверка: {hint}\n"
        f"{module_status_line(m, meff.heartbeat_timeout_sec, meff.slow_ms)}\n"
        f"🕑 Отчёт: {m.get('last_ok_at') or '—'}\n"
        f"⚙️ {'свои (' + str(ov) + ')' if ov else 'общие'}"
    )
    return text, kb.website_module_detail(mid, bool(m["enabled"]))


async def _render_target_detail(db: Database, tid: int) -> tuple[str, InlineKeyboardMarkup]:
    t = await queries.get_router_target(db, tid)
    if not t:
        return "❌ Не найдено", kb.back_to_main_keyboard()
    rid = int(t["router_id"])
    settings = await queries.load_all_settings(db)
    teff = queries.effective_monitor_for_entity(settings, t)
    en = "✅ вкл" if t["enabled"] else "⏸ выкл"
    ov = queries.count_override_keys(t.get("settings_override"))
    text = (
        f"📡 <b>{t['display_name']}</b>\n"
        f"id={tid} · router {rid} · {en}\n"
        f"📍 {t['address']}\n"
        f"{target_status_line(t, teff.heartbeat_timeout_sec)}\n"
        f"🕑 Отчёт: {t.get('last_ok_at') or '—'}\n"
        f"⚙️ {'свои (' + str(ov) + ')' if ov else 'общие'}"
    )
    return text, kb.site_target_detail(tid, rid, bool(t["enabled"]))


async def _render_entity_settings_screen(
    db: Database, screen_key: str
) -> tuple[str, InlineKeyboardMarkup]:
    parts = screen_key.split(":")
    action = parts[1]
    if action == "home":
        kind, eid = parts[2], int(parts[3])
        row = await get_entity_display_name(db, kind, eid)
        if row is None:
            return "❌ Объект не найден", kb.back_to_main_keyboard()
        entity = await _fetch_entity_row(db, kind, eid)
        ov = queries.count_override_keys(entity.get("settings_override") if entity else None)
        label = ENTITY_KIND_LABELS.get(kind, kind)
        if ov:
            mode_line = f"Сейчас задано <b>своих</b> параметров: {ov}."
        else:
            mode_line = "Сейчас действуют только <b>глобальные</b> настройки (меню «Настройки»)."
        text = (
            f"⚙️ <b>Настройки</b> · {label}\n"
            f"<b>{row}</b> (id={eid})\n\n"
            f"{mode_line}\n"
            "«Глобальные настройки» — дефолты для всех объектов.\n"
            "«Мониторинг» / «Уведомления» — только для этого объекта.\n"
            + (
                "У модулей без своих ✎ действуют настройки сайта поверх глобальных."
                if kind == "website"
                else ""
            )
        )
        return text, kb.entity_settings_menu(kind, eid, ov)
    if action == "grp":
        gid, kind, eid = parts[2], parts[3], int(parts[4])
        entity = await _fetch_entity_row(db, kind, eid)
        if not entity:
            return "❌ Объект не найден", kb.back_to_main_keyboard()
        override = await queries.get_entity_settings_override(db, kind, eid)
        gtitle = "Мониторинг и алерты" if gid == "monitor" else "Уведомления"
        text = f"⚙️ <b>{gtitle}</b> · id={eid}\n\n✎ — своё значение"
        return text, kb.entity_settings_group_menu(kind, eid, gid, set(override.keys()))
    if action == "key":
        key, kind, eid = parts[2], parts[3], int(parts[4])
        override = await queries.get_entity_settings_override(db, kind, eid)
        prompt = await format_entity_key_change_prompt(db, kind, eid, key)
        if len(prompt) > 4000:
            prompt = prompt[:3990] + "…"
        return (
            f"⚙️ <b>{META[key].get('title', key)}</b>\n\n{prompt}",
            kb.entity_setting_detail_keyboard(kind, eid, key, key in override),
        )
    if action == "edit":
        key, kind, eid = parts[2], parts[3], int(parts[4])
        prompt = await format_entity_key_change_prompt(db, kind, eid, key)
        if len(prompt) > 4000:
            prompt = prompt[:3990] + "…"
        return (
            f"⚙️ <b>{META[key].get('title', key)}</b>\n\n{prompt}",
            kb.entity_setting_input_keyboard(kind, eid, key),
        )
    return "❌ Неизвестный экран", kb.back_to_main_keyboard()


async def _fetch_entity_row(db: Database, kind: str, entity_id: int) -> dict | None:
    if kind == "bot":
        return await queries.get_monitored_bot(db, entity_id)
    if kind == "router":
        return await queries.get_monitored_router(db, entity_id)
    if kind == "target":
        return await queries.get_router_target(db, entity_id)
    if kind == "website":
        return await queries.get_monitored_website(db, entity_id)
    if kind == "module":
        return await queries.get_website_module(db, entity_id)
    return None


async def goto_screen_cq(
    cq: Any,
    state: FSMContext,
    db: Database,
    screen_key: str,
    *,
    push: bool = True,
    pop: int = 0,
    hb_server: HeartbeatServer | None = None,
    failures_args: str | None = None,
) -> None:
    from botping.bot.panel import apply_screen_nav, render_panel_cq

    await apply_screen_nav(state, screen_key, push=push, pop=pop)

    text, markup = await render_screen(
        screen_key,
        db,
        hb_server=hb_server,
        state=state,
        failures_args=failures_args,
    )
    await render_panel_cq(cq, state, db, text, reply_markup=markup)


async def goto_screen_message(
    message: Any,
    state: FSMContext,
    db: Database,
    screen_key: str,
    *,
    push: bool = False,
    pop: int = 0,
    hb_server: HeartbeatServer | None = None,
    failures_args: str | None = None,
) -> None:
    from botping.bot.panel import apply_screen_nav, render_panel_message

    await apply_screen_nav(state, screen_key, push=push, pop=pop)

    text, markup = await render_screen(
        screen_key,
        db,
        hb_server=hb_server,
        state=state,
        failures_args=failures_args,
    )
    await render_panel_message(message, state, db, text, reply_markup=markup)
