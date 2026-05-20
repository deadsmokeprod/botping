from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot.common import public_host_from_env
from botping.bot.panel import render_panel_message, send_reference, send_reference_document
from botping.bot import panel_screens as screens
from botping.bot.states import AddWebsiteModuleStates, AddWebsiteStates
from botping.bot.websites_formatting import WEBSITES_MENU_INTRO
from botping.db import queries
from botping.db.pool import Database, generate_heartbeat_secret
from botping.heartbeat_server import HeartbeatServer
from botping.site_agent.snippet import build_site_agent_snippet

SETUP_CHECKLIST = (
    "Чеклист на сервере сайта:\n"
    "• Python 3.10+ и pip install httpx\n"
    "• Сохраните файл <code>botping-site-agent.py</code> (ниже в чате)\n"
    "• cron: <code>*/1 * * * * python3 /path/botping-site-agent.py</code>\n"
    "• Интернет до VPS Botping\n"
    "• 📊 Статус в боте"
)

SITE_AGENT_FILENAME = "botping-site-agent.py"


async def _send_website_setup(bot, chat_id: int, db: Database, website_id: int) -> None:
    w = await queries.get_monitored_website(db, website_id)
    if not w:
        return
    modules = await queries.list_website_modules(db, website_id)
    snippet = build_site_agent_snippet(
        public_host_from_env(),
        str(w["heartbeat_secret"]),
        str(w["host"]),
        modules,
    )
    await send_reference(
        bot,
        chat_id,
        f"🔧 <b>Установка агента</b> — {w['display_name']}\n"
        f"Домен: <code>{w['host']}</code>\n\n{SETUP_CHECKLIST}",
    )
    await send_reference_document(
        bot,
        chat_id,
        BufferedInputFile(
            snippet.encode("utf-8"),
            filename=SITE_AGENT_FILENAME,
        ),
        caption=f"📎 {SITE_AGENT_FILENAME} — скачайте и положите на сервер сайта",
    )


def register_websites_handlers(router: Router) -> None:
    @router.callback_query(F.data == "menu:websites")
    async def on_menu_websites(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "websites")
        await cq.answer()

    @router.callback_query(F.data == "web:add")
    async def on_web_add(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(AddWebsiteStates.waiting_name)
        await screens.goto_screen_cq(cq, state, db, "add_website_name")
        await cq.answer()

    @router.message(AddWebsiteStates.waiting_name, F.text)
    async def on_add_website_name(message: Message, state: FSMContext, db: Database) -> None:
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
        await state.update_data(website_name=name)
        await state.set_state(AddWebsiteStates.waiting_host)
        await screens.goto_screen_message(
            message, state, db, "add_website_host", push=False
        )

    @router.message(AddWebsiteStates.waiting_host, F.text)
    async def on_add_website_host(
        message: Message,
        state: FSMContext,
        db: Database,
        hb_server: HeartbeatServer | None = None,
    ) -> None:
        host_raw = (message.text or "").strip()
        name = str((await state.get_data()).get("website_name") or "").strip()
        if not name or not host_raw:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "websites", push=False)
            return
        host = queries.normalize_website_host(host_raw)
        if not re.match(r"^[\w.\-]+$", host, re.ASCII):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Недопустимый домен (например mongol.pro).",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, secret):
            secret = generate_heartbeat_secret()
        try:
            new_id = await queries.insert_monitored_website(db, name, host, secret)
        except Exception:
            await render_panel_message(
                message,
                state,
                db,
                "❌ Не удалось (возможно, домен уже есть).",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await state.set_state(None)
        await screens.goto_screen_message(
            message, state, db, f"website_created:{new_id}", push=False
        )

    @router.callback_query(F.data.startswith("web:view:"))
    async def on_web_view(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        wid = int(cq.data.split(":")[2])
        await screens.goto_screen_cq(cq, state, db, f"website:{wid}")
        await cq.answer()

    @router.callback_query(F.data.startswith("web:setup:"))
    async def on_web_setup(cq: CallbackQuery, db: Database) -> None:
        wid = int(cq.data.split(":")[2])
        w = await queries.get_monitored_website(db, wid)
        if not w:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        chat_id = cq.message.chat.id if cq.message else 0
        await _send_website_setup(cq.bot, chat_id, db, wid)
        await cq.answer()

    @router.callback_query(F.data.startswith("web:secret:"))
    async def on_web_secret(cq: CallbackQuery, db: Database) -> None:
        wid = int(cq.data.split(":")[2])
        w = await queries.get_monitored_website(db, wid)
        if not w:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            f"🔑 Секрет <b>{w['display_name']}</b> (id={wid}):\n"
            f"<code>{w['heartbeat_secret']}</code>",
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("web:rotate:"))
    async def on_web_rotate(
        cq: CallbackQuery, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        wid = int(cq.data.split(":")[2])
        w = await queries.get_monitored_website(db, wid)
        if not w:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        old = str(w["heartbeat_secret"])
        new_secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, new_secret, exclude=old):
            new_secret = generate_heartbeat_secret()
        await queries.regenerate_website_heartbeat_secret(db, wid, new_secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            f"🔄 Новый секрет для <b>{w['display_name']}</b>:\n"
            f"<code>{new_secret}</code>\n\n"
            "Обновите скрипт на сервере сайта.",
        )
        await cq.answer("✅ Секрет обновлён")

    @router.callback_query(F.data.startswith("web:toggle:"))
    async def on_web_toggle(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        wid = int(cq.data.split(":")[2])
        w = await queries.get_monitored_website(db, wid)
        if not w:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await queries.update_website_enabled(db, wid, not bool(w["enabled"]))
        await screens.goto_screen_cq(cq, state, db, f"website:{wid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("web:delask:"))
    async def on_web_delask(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        wid = int(cq.data.split(":")[2])
        w = await queries.get_monitored_website(db, wid)
        if not w:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await screens.goto_screen_cq(cq, state, db, f"website_del:{wid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("web:del:"))
    async def on_web_del(
        cq: CallbackQuery, state: FSMContext, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        wid = int(cq.data.split(":")[2])
        await queries.delete_monitored_website(db, wid)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await screens.goto_screen_cq(cq, state, db, "websites", push=False, pop=1)
        await cq.answer("✅ Удалён")

    @router.callback_query(F.data.startswith("web:mod_add:"))
    async def on_mod_add(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        wid = int(cq.data.split(":")[2])
        await state.update_data(module_website_id=wid)
        await state.set_state(AddWebsiteModuleStates.waiting_name)
        await screens.goto_screen_cq(cq, state, db, f"add_webmod_name:{wid}")
        await cq.answer()

    @router.message(AddWebsiteModuleStates.waiting_name, F.text)
    async def on_mod_name(message: Message, state: FSMContext, db: Database) -> None:
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
        await state.update_data(module_name=name)
        await state.set_state(AddWebsiteModuleStates.waiting_hint)
        data = await state.get_data()
        wid = int(data.get("module_website_id") or 0)
        await screens.goto_screen_message(
            message, state, db, f"add_webmod_hint:{wid}", push=False
        )

    @router.message(AddWebsiteModuleStates.waiting_hint, F.text)
    async def on_mod_hint(message: Message, state: FSMContext, db: Database) -> None:
        hint = (message.text or "").strip()
        data = await state.get_data()
        wid = int(data.get("module_website_id") or 0)
        name = str(data.get("module_name") or "").strip()
        if not wid or not name:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "websites", push=False)
            return
        if hint == "-":
            hint = ""
        try:
            mid = await queries.insert_website_module(
                db, wid, name, hint or None
            )
        except Exception:
            await render_panel_message(
                message,
                state,
                db,
                "❌ Не удалось (возможно, имя уже есть).",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        await state.set_state(None)
        text, markup = await screens.render_screen(f"website:{wid}", db)
        await render_panel_message(
            message,
            state,
            db,
            f"✅ Модуль <b>{name}</b>, id={mid}.\n\n{text}",
            reply_markup=markup,
        )

    @router.callback_query(F.data.startswith("web:mview:"))
    async def on_mod_view(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        mid = int(cq.data.split(":")[2])
        await screens.goto_screen_cq(cq, state, db, f"webmod:{mid}")
        await cq.answer()

    @router.callback_query(F.data.startswith("web:mtoggle:"))
    async def on_mod_toggle(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        mid = int(cq.data.split(":")[2])
        m = await queries.get_website_module(db, mid)
        if not m:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await queries.update_website_module_enabled(db, mid, not bool(m["enabled"]))
        await screens.goto_screen_cq(cq, state, db, f"webmod:{mid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("web:mdelask:"))
    async def on_mod_delask(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        mid = int(cq.data.split(":")[2])
        m = await queries.get_website_module(db, mid)
        if not m:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await screens.goto_screen_cq(cq, state, db, f"webmod_del:{mid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("web:mdel:"))
    async def on_mod_del(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        mid = int(cq.data.split(":")[2])
        m = await queries.get_website_module(db, mid)
        if not m:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        wid = int(m["website_id"])
        await queries.delete_website_module(db, mid)
        await screens.goto_screen_cq(cq, state, db, f"website:{wid}", push=False)
        await cq.answer("✅ Удалено")
