from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot.common import (
    chunk_text,
    format_age_ru,
    heartbeat_age_sec,
    mask_secret,
    public_host_from_env,
)
from botping.bot.states import AddRouterStates, AddTargetStates
from botping.bot.ui import edit_or_answer
from botping.db import queries
from botping.db.pool import Database, generate_heartbeat_secret
from botping.heartbeat_server import HeartbeatServer
from botping.mikrotik.snippet import build_routeros_snippet, build_routeros_uplink_events_snippet
from botping.router_events import internet_channel_label
from botping.monitor.router_monitor import _target_alive

ROUTERS_MENU_INTRO = (
    "Роутеры и устройства (MikroTik)\n\n"
    "Как настроить:\n"
    "1) Добавьте роутер (имя площадки).\n"
    "2) Укажите устройства в LAN — IP, которые MikroTik будет пинговать.\n"
    "3) Установите скрипт на роутер (кнопка «Установка на MikroTik»).\n"
    "4) Настройте уведомления WAN/LTE (кнопка в карточке роутера).\n"
    "5) Проверьте /status — роутер и устройства должны быть ЖИВ.\n"
)

SETUP_CHECKLIST = (
    "Чеклист на роутере (Winbox / WebFig):\n"
    "• System → Scripts → создать/заменить botping-lan\n"
    "• System → Scheduler → каждые 30 с, policy: read,write,policy,test\n"
    "• Убедиться, что с роутера есть интернет до VPS\n"
    "• В боте: /status — блок «Роутеры и устройства»"
)

UPLINK_SETUP_INTRO = (
    "Уведомления о переключении интернета WAN ↔ LTE\n\n"
    "Это отдельные сообщения в Telegram (не путать с «устройство в LAN недоступно»).\n\n"
    "Чеклист:\n"
    "1) System → Scripts — создать botping-internet-lte и botping-internet-wan\n"
    "   (скопируйте оба блока ниже).\n"
    "2) В скриптах Check_Internet и UPLink_WAN — одна строка вызова\n"
    "   (см. конец сообщения).\n"
    "3) Проверка: Run Script → botping-internet-lte — в Telegram должно прийти сообщение.\n"
    "4) Журнал — кнопка «Журнал переключений» в карточке роутера."
)


def _target_status_line(t: dict, hb_timeout: int) -> str:
    alive, err = _target_alive(t, hb_timeout)
    if alive:
        ms = t.get("last_latency_ms")
        return f"ЖИВ{f', {ms} ms' if ms is not None else ''}"
    return f"НЕТ ({err or '?'})"


def _target_button_label(t: dict, hb_timeout: int) -> str:
    st = _target_status_line(t, hb_timeout)
    name = str(t["display_name"])[:20]
    addr = str(t["address"])
    return f"{st} · {name} · {addr}"


async def _router_summary_label(
    db: Database, r: dict, hb_timeout: int
) -> str:
    rid = int(r["id"])
    age = heartbeat_age_sec(r)
    if age is None:
        r_st = "нет пингов"
    elif age <= hb_timeout:
        r_st = "ЖИВ"
    else:
        r_st = "НЕТ пинга"
    if not r["enabled"]:
        r_st = f"{r_st}, выкл"
    targets = await queries.list_router_targets(db, rid, enabled_only=True)
    if not targets:
        ok_s = "0 целей"
    else:
        ok_n = sum(1 for t in targets if _target_alive(t, hb_timeout)[0])
        ok_s = f"{ok_n}/{len(targets)} OK"
    name = str(r["display_name"])[:28]
    return f"{name} · {r_st} · {ok_s}"


async def _build_router_menu_rows(db: Database) -> list[tuple[int, str]]:
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    rows = await queries.list_monitored_routers(db)
    out: list[tuple[int, str]] = []
    for r in rows:
        out.append((int(r["id"]), await _router_summary_label(db, r, hb_timeout)))
    return out


async def _target_buttons_for_router(
    db: Database, router_id: int, hb_timeout: int
) -> list[tuple[int, str]]:
    targets = await queries.list_router_targets(db, router_id)
    return [
        (int(t["id"]), _target_button_label(t, hb_timeout))
        for t in targets
    ]


async def _format_site_detail(db: Database, router_id: int) -> str | None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return None
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    age = heartbeat_age_sec(r)
    if age is None:
        hb_state = "нет ни одного heartbeat"
    elif age <= hb_timeout:
        hb_state = f"ЖИВ, пинг {format_age_ru(age)} назад"
    else:
        hb_state = f"НЕДОСТУПЕН, нет пинга {format_age_ru(age)}"
    last_ev = await queries.get_latest_router_event(db, router_id)
    if last_ev:
        ch = internet_channel_label(str(last_ev["event_type"]))
        channel_line = f"Канал интернета: {ch}" if ch else "Канал интернета: (см. журнал)"
        last_sw = f"Последнее переключение: {last_ev['created_at']}"
    else:
        channel_line = "Канал интернета: ещё не было переключений"
        last_sw = "Последнее переключение: —"
    lines = [
        f"Роутер: {r['display_name']}",
        f"id={router_id}",
        f"Мониторинг: {'включён' if r['enabled'] else 'выключен (пинги всё равно принимаются)'}",
        f"Heartbeat: {hb_state}",
        channel_line,
        last_sw,
        f"Последний пинг: {r.get('last_heartbeat_at') or '—'}",
        f"С IP: {r.get('last_heartbeat_ip') or '—'}",
        f"Секрет: {mask_secret(str(r['heartbeat_secret']))}",
        f"URL: {public_host_from_env()}/heartbeat",
        "",
        "Устройства в LAN (нажмите кнопку ниже для карточки):",
    ]
    targets = await queries.list_router_targets(db, router_id)
    if not targets:
        lines.append("  (нет — нажмите «+ Устройство в LAN»)")
    else:
        for t in targets:
            tid = int(t["id"])
            inc = await queries.get_open_router_target_incident(db, tid)
            inc_s = " ИНЦИДЕНТ" if inc else ""
            st = "вкл" if t["enabled"] else "выкл"
            lines.append(
                f"  · [{tid}] {t['display_name']} {t['address']} ({st}): "
                f"{_target_status_line(t, hb_timeout)}{inc_s}"
            )
    lines.append("")
    if targets:
        lines.append("После смены списка IP обновите скрипт: «Установка на MikroTik».")
    else:
        lines.append("Шаг 2: добавьте устройство (IP в LAN), затем «Установка на MikroTik».")
    return "\n".join(lines)


async def _send_uplink_setup(
    message_or_cq: Message | CallbackQuery,
    db: Database,
    router_id: int,
) -> None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return
    snippet = build_routeros_uplink_events_snippet(
        public_host_from_env(), str(r["heartbeat_secret"])
    )
    header = f"{UPLINK_SETUP_INTRO}\n\nРоутер: {r['display_name']}\n\n"
    if isinstance(message_or_cq, CallbackQuery):
        msg = message_or_cq.message
        assert msg is not None
        await msg.answer(header)
        for part in chunk_text(snippet):
            await msg.answer(f"<pre>{part}</pre>", parse_mode="HTML")
    else:
        await message_or_cq.answer(header)
        for part in chunk_text(snippet):
            await message_or_cq.answer(f"<pre>{part}</pre>", parse_mode="HTML")


async def _format_event_log(db: Database, router_id: int) -> str | None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return None
    events = await queries.list_router_events(db, router_id, limit=20)
    lines = [f"Журнал переключений — {r['display_name']}", ""]
    if not events:
        lines.append("Записей пока нет.")
        lines.append("Настройте скрипты: «Переключение WAN/LTE» в карточке роутера.")
    else:
        for ev in events:
            lines.append(f"• {ev['created_at']}")
            lines.append(f"  {ev['message']}")
    return "\n".join(lines)


async def _send_router_setup(
    message_or_cq: Message | CallbackQuery,
    db: Database,
    router_id: int,
) -> None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return
    targets = await queries.list_router_targets(db, router_id)
    snippet = build_routeros_snippet(
        public_host_from_env(), str(r["heartbeat_secret"]), targets
    )
    header = (
        f"Шаг 3: установка на MikroTik — {r['display_name']}\n\n"
        f"{SETUP_CHECKLIST}\n\n"
        f"Script (System → Scripts → botping-lan):\n"
    )
    if isinstance(message_or_cq, CallbackQuery):
        msg = message_or_cq.message
        assert msg is not None
        await msg.answer(header)
        for part in chunk_text(snippet):
            await msg.answer(f"<pre>{part}</pre>", parse_mode="HTML")
    else:
        await message_or_cq.answer(header)
        for part in chunk_text(snippet):
            await message_or_cq.answer(f"<pre>{part}</pre>", parse_mode="HTML")


async def _show_routers_menu(cq: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    menu_rows = await _build_router_menu_rows(db)
    text = ROUTERS_MENU_INTRO
    if not menu_rows:
        text += "\nПока нет роутеров — нажмите «+ Добавить роутер»."
    await edit_or_answer(cq, text, reply_markup=kb.routers_menu(menu_rows))


def register_sites_handlers(router: Router) -> None:
    @router.callback_query(F.data.in_({"menu:routers", "menu:sites"}))
    async def on_menu_routers(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await _show_routers_menu(cq, db, state)
        await cq.answer()

    @router.callback_query(F.data == "site:add")
    async def on_site_add(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AddRouterStates.waiting_name)
        await cq.message.answer(
            "Шаг 1/3: введите имя роутера или площадки\n"
            "(например «Офис» или «192.168.99.1»)."
        )
        await cq.answer()

    @router.message(AddRouterStates.waiting_name, F.text)
    async def on_add_router_name(
        message: Message, state: FSMContext, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        name = (message.text or "").strip()
        if not name:
            await message.answer("Имя не может быть пустым.")
            return
        secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, secret):
            secret = generate_heartbeat_secret()
        new_id = await queries.insert_monitored_router(db, name, secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await state.clear()
        await message.answer(
            f"Роутер «{name}» создан (id={new_id}).\n\n"
            "Шаг 2/3: добавьте устройства в LAN (IP, которые MikroTik будет пинговать).\n"
            "Шаг 3/3: установите скрипт на роутер (кнопка в карточке).",
            reply_markup=kb.router_after_create(new_id),
        )

    @router.callback_query(F.data.startswith("site:view:"))
    async def on_site_view(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        text = await _format_site_detail(db, rid)
        if not text:
            await cq.answer("Не найден", show_alert=True)
            return
        r = await queries.get_monitored_router(db, rid)
        assert r is not None
        settings = await queries.load_all_settings(db)
        hb_timeout = int(settings["heartbeat_timeout_sec"])
        target_btns = await _target_buttons_for_router(db, rid, hb_timeout)
        await edit_or_answer(
            cq,
            text,
            reply_markup=kb.router_detail(rid, bool(r["enabled"]), target_btns),
        )
        await cq.answer()

    @router.callback_query(
        F.data.startswith("site:setup:") | F.data.startswith("site:snippet:")
    )
    async def on_site_setup(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        targets = await queries.list_router_targets(db, rid)
        if not targets:
            await cq.answer(
                "Сначала добавьте хотя бы одно устройство (IP в LAN).",
                show_alert=True,
            )
            return
        await _send_router_setup(cq, db, rid)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:uplink:"))
    async def on_site_uplink(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await _send_uplink_setup(cq, db, rid)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:evlog:"))
    async def on_site_evlog(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        text = await _format_event_log(db, rid)
        if not text:
            await cq.answer("Не найден", show_alert=True)
            return
        await cq.message.answer(text)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:secret:"))
    async def on_site_secret(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await cq.message.answer(
            f"Секрет роутера {r['display_name']} (id={rid}):\n"
            f"<code>{r['heartbeat_secret']}</code>",
            parse_mode="HTML",
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:rotate:"))
    async def on_site_rotate(
        cq: CallbackQuery, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        old = str(r["heartbeat_secret"])
        new_secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, new_secret, exclude=old):
            new_secret = generate_heartbeat_secret()
        await queries.regenerate_router_heartbeat_secret(db, rid, new_secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await cq.message.answer(
            f"Новый секрет для {r['display_name']}:\n<code>{new_secret}</code>\n"
            "Обновите скрипты на MikroTik («Установка на MikroTik» и «Переключение WAN/LTE»).",
            parse_mode="HTML",
        )
        await cq.answer("Секрет обновлён")

    @router.callback_query(F.data.startswith("site:toggle:"))
    async def on_site_toggle(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await queries.update_router_enabled(db, rid, not bool(r["enabled"]))
        await on_site_view(cq, db)

    @router.callback_query(F.data.startswith("site:delask:"))
    async def on_site_delask(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await edit_or_answer(
            cq,
            f"Удалить роутер «{r['display_name']}» и все устройства?",
            reply_markup=kb.confirm_site_delete(rid),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:del:"))
    async def on_site_del(
        cq: CallbackQuery, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        rid = int(cq.data.split(":")[2])
        await queries.delete_monitored_router(db, rid)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        menu_rows = await _build_router_menu_rows(db)
        await edit_or_answer(cq, "Роутер удалён.", reply_markup=kb.routers_menu(menu_rows))
        await cq.answer()

    @router.callback_query(F.data.startswith("site:target_add:"))
    async def on_target_add(cq: CallbackQuery, state: FSMContext) -> None:
        rid = int(cq.data.split(":")[2])
        await state.update_data(target_router_id=rid)
        await state.set_state(AddTargetStates.waiting_name)
        await cq.message.answer(
            "Имя устройства (например «Камера вход», «ПК офис»):"
        )
        await cq.answer()

    @router.message(AddTargetStates.waiting_name, F.text)
    async def on_target_name(message: Message, state: FSMContext) -> None:
        name = (message.text or "").strip()
        if not name:
            await message.answer("Имя не может быть пустым.")
            return
        await state.update_data(target_name=name)
        await state.set_state(AddTargetStates.waiting_address)
        await message.answer("IP или hostname в LAN (например 192.168.88.10):")

    @router.message(AddTargetStates.waiting_address, F.text)
    async def on_target_address(message: Message, state: FSMContext, db: Database) -> None:
        addr = (message.text or "").strip()
        data = await state.get_data()
        rid = int(data.get("target_router_id") or 0)
        name = str(data.get("target_name") or "").strip()
        if not rid or not name or not addr:
            await state.clear()
            await message.answer("Сессия сброшена.")
            return
        if not re.match(r"^[\w.\-:/]+$", addr, re.ASCII):
            await message.answer("Недопустимый адрес. Используйте IP или hostname.")
            return
        try:
            tid = await queries.insert_router_target(db, rid, name, addr)
        except Exception:
            await message.answer(
                "Не удалось добавить (возможно, такой адрес уже есть у этого роутера)."
            )
            return
        await state.clear()
        await message.answer(
            f"Устройство добавлено: {name} ({addr}), id={tid}.\n"
            "Добавить ещё одно?",
            reply_markup=kb.target_added_more(rid),
        )

    @router.callback_query(F.data.startswith("site:tview:"))
    async def on_target_view(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        rid = int(t["router_id"])
        settings = await queries.load_all_settings(db)
        hb_timeout = int(settings["heartbeat_timeout_sec"])
        text = (
            f"Устройство: {t['display_name']}\n"
            f"id={tid}, router_id={rid}\n"
            f"Адрес: {t['address']}\n"
            f"Статус: {_target_status_line(t, hb_timeout)}\n"
            f"Последний отчёт: {t.get('last_ok_at') or '—'}"
        )
        await edit_or_answer(
            cq, text, reply_markup=kb.site_target_detail(tid, rid, bool(t["enabled"]))
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:ttoggle:"))
    async def on_target_toggle(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        await queries.update_router_target_enabled(db, tid, not bool(t["enabled"]))
        await on_target_view(cq, db)

    @router.callback_query(F.data.startswith("site:tdelask:"))
    async def on_target_delask(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        await edit_or_answer(
            cq,
            f"Удалить «{t['display_name']}» ({t['address']})?",
            reply_markup=kb.confirm_target_delete(tid, int(t["router_id"])),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:tdel:"))
    async def on_target_del(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        rid = int(t["router_id"])
        await queries.delete_router_target(db, tid)
        await on_site_view(cq, db)
