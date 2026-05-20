from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot.common import chunk_text, public_host_from_env
from botping.bot.formatting import chunk_text as fmt_chunk
from botping.bot.panel import render_panel_message, send_reference
from botping.bot import panel_screens as screens
from botping.bot.sites_formatting import format_event_log
from botping.bot.states import AddRouterStates, AddTargetStates
from botping.db import queries
from botping.db.pool import Database, generate_heartbeat_secret
from botping.heartbeat_server import HeartbeatServer
from botping.mikrotik.snippet import build_routeros_snippet, build_routeros_uplink_events_snippet

SETUP_CHECKLIST = (
    "Чеклист на роутере (Winbox / WebFig):\n"
    "• System → Scripts → botping-lan\n"
    "• System → Scheduler → каждые 30 с\n"
    "• Интернет до VPS\n"
    "• 📊 Статус в боте"
)

UPLINK_SETUP_INTRO = (
    "📶 <b>WAN/LTE уведомления</b>\n\n"
    "Отдельные сообщения в Telegram при переключении канала.\n"
    "1️⃣ Scripts: botping-internet-lte / botping-internet-wan\n"
    "2️⃣ Вызов в Check_Internet и UPLink_WAN\n"
    "3️⃣ Run Script → проверка в Telegram"
)


async def _send_uplink_setup(bot, chat_id: int, db: Database, router_id: int) -> None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return
    snippet = build_routeros_uplink_events_snippet(
        public_host_from_env(), str(r["heartbeat_secret"])
    )
    await send_reference(
        bot,
        chat_id,
        f"{UPLINK_SETUP_INTRO}\n\n🌐 Роутер: <b>{r['display_name']}</b>\n",
    )
    for part in fmt_chunk(snippet):
        await send_reference(bot, chat_id, f"<pre>{part}</pre>")


async def _send_router_setup(bot, chat_id: int, db: Database, router_id: int) -> None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return
    targets = await queries.list_router_targets(db, router_id)
    snippet = build_routeros_snippet(
        public_host_from_env(), str(r["heartbeat_secret"]), targets
    )
    await send_reference(
        bot,
        chat_id,
        f"🔧 <b>Установка MikroTik</b> — {r['display_name']}\n\n"
        f"{SETUP_CHECKLIST}\n\n"
        f"Script botping-lan:",
    )
    for part in fmt_chunk(snippet):
        await send_reference(bot, chat_id, f"<pre>{part}</pre>")


def register_sites_handlers(router: Router) -> None:
    @router.callback_query(F.data.in_({"menu:routers", "menu:sites"}))
    async def on_menu_routers(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "routers")
        await cq.answer()

    @router.callback_query(F.data == "site:add")
    async def on_site_add(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(AddRouterStates.waiting_name)
        await screens.goto_screen_cq(cq, state, db, "add_router_name")
        await cq.answer()

    @router.message(AddRouterStates.waiting_name, F.text)
    async def on_add_router_name(
        message: Message,
        state: FSMContext,
        db: Database,
        hb_server: HeartbeatServer | None = None,
    ) -> None:
        name = (message.text or "").strip()
        if not name:
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Имя не может быть пустым.",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, secret):
            secret = generate_heartbeat_secret()
        new_id = await queries.insert_monitored_router(db, name, secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await state.set_state(None)
        await screens.goto_screen_message(
            message, state, db, f"router_created:{new_id}", push=False
        )

    @router.callback_query(F.data.startswith("site:view:"))
    async def on_site_view(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        await screens.goto_screen_cq(cq, state, db, f"router:{rid}")
        await cq.answer()

    @router.callback_query(
        F.data.startswith("site:setup:") | F.data.startswith("site:snippet:")
    )
    async def on_site_setup(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        targets = await queries.list_router_targets(db, rid)
        if not targets:
            await cq.answer(
                "Сначала добавьте устройство (IP в LAN).",
                show_alert=True,
            )
            return
        chat_id = cq.message.chat.id if cq.message else 0
        await _send_router_setup(cq.bot, chat_id, db, rid)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:uplink:"))
    async def on_site_uplink(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        chat_id = cq.message.chat.id if cq.message else 0
        await _send_uplink_setup(cq.bot, chat_id, db, rid)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:evlog:"))
    async def on_site_evlog(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        text = await format_event_log(db, rid)
        if not text:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        chat_id = cq.message.chat.id if cq.message else 0
        for part in chunk_text(text):
            await send_reference(cq.bot, chat_id, part)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:secret:"))
    async def on_site_secret(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            f"🔑 Секрет <b>{r['display_name']}</b> (id={rid}):\n"
            f"<code>{r['heartbeat_secret']}</code>",
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:rotate:"))
    async def on_site_rotate(
        cq: CallbackQuery, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        old = str(r["heartbeat_secret"])
        new_secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, new_secret, exclude=old):
            new_secret = generate_heartbeat_secret()
        await queries.regenerate_router_heartbeat_secret(db, rid, new_secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            f"🔄 Новый секрет для <b>{r['display_name']}</b>:\n"
            f"<code>{new_secret}</code>\n\n"
            "Обновите скрипты на MikroTik.",
        )
        await cq.answer("✅ Секрет обновлён")

    @router.callback_query(F.data.startswith("site:toggle:"))
    async def on_site_toggle(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await queries.update_router_enabled(db, rid, not bool(r["enabled"]))
        await screens.goto_screen_cq(cq, state, db, f"router:{rid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:delask:"))
    async def on_site_delask(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await screens.goto_screen_cq(cq, state, db, f"router_del:{rid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:del:"))
    async def on_site_del(
        cq: CallbackQuery, state: FSMContext, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        rid = int(cq.data.split(":")[2])
        await queries.delete_monitored_router(db, rid)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await screens.goto_screen_cq(cq, state, db, "routers", push=False, pop=1)
        await cq.answer("✅ Удалён")

    @router.callback_query(F.data.startswith("site:target_add:"))
    async def on_target_add(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        await state.update_data(target_router_id=rid)
        await state.set_state(AddTargetStates.waiting_name)
        await screens.goto_screen_cq(cq, state, db, f"add_target_name:{rid}")
        await cq.answer()

    @router.message(AddTargetStates.waiting_name, F.text)
    async def on_target_name(message: Message, state: FSMContext, db: Database) -> None:
        name = (message.text or "").strip()
        if not name:
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Имя не может быть пустым.",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        await state.update_data(target_name=name)
        await state.set_state(AddTargetStates.waiting_address)
        data = await state.get_data()
        rid = int(data.get("target_router_id") or 0)
        await screens.goto_screen_message(
            message, state, db, f"add_target_addr:{rid}", push=False
        )

    @router.message(AddTargetStates.waiting_address, F.text)
    async def on_target_address(message: Message, state: FSMContext, db: Database) -> None:
        addr = (message.text or "").strip()
        data = await state.get_data()
        rid = int(data.get("target_router_id") or 0)
        name = str(data.get("target_name") or "").strip()
        if not rid or not name or not addr:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "routers", push=False)
            return
        if not re.match(r"^[\w.\-:/]+$", addr, re.ASCII):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Недопустимый адрес.",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        try:
            tid = await queries.insert_router_target(db, rid, name, addr)
        except Exception:
            await render_panel_message(
                message,
                state,
                db,
                "❌ Не удалось (возможно, адрес уже есть).",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        await state.set_state(None)
        text, markup = await screens.render_screen(f"router:{rid}", db)
        await render_panel_message(
            message,
            state,
            db,
            f"✅ Устройство <b>{name}</b> ({addr}), id={tid}.\n\n{text}",
            reply_markup=markup,
        )

    @router.callback_query(F.data.startswith("site:tview:"))
    async def on_target_view(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        await screens.goto_screen_cq(cq, state, db, f"target:{tid}")
        await cq.answer()

    @router.callback_query(F.data.startswith("site:ttoggle:"))
    async def on_target_toggle(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await queries.update_router_target_enabled(db, tid, not bool(t["enabled"]))
        await screens.goto_screen_cq(cq, state, db, f"target:{tid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:tdelask:"))
    async def on_target_delask(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await screens.goto_screen_cq(cq, state, db, f"target_del:{tid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("site:tdel:"))
    async def on_target_del(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        rid = int(t["router_id"])
        await queries.delete_router_target(db, tid)
        await screens.goto_screen_cq(cq, state, db, f"router:{rid}", push=False)
        await cq.answer("✅ Удалено")
