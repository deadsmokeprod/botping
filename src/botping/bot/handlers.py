from __future__ import annotations

import json
import logging
import re

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import BufferedInputFile, CallbackQuery, Message, ReplyKeyboardRemove

from botping.bot import keyboards as kb
from botping.bot.commands import refresh_chat_commands_and_menu
from botping.bot.formatting import chunk_text, heartbeat_snippet
from botping.bot.handlers_entity_settings import register_entity_settings_handlers
from botping.bot.handlers_names import register_names_handlers
from botping.bot.handlers_sites import register_sites_handlers
from botping.bot.handlers_websites import register_websites_handlers
from botping.bot.panel import (
    refresh_interface,
    render_panel_message,
    send_reference,
    send_reference_document,
)
from botping.bot.settings_help import META
from botping.bot import panel_screens as screens
from botping.bot.panel import pop_nav
from botping.bot.reports.build import build_availability_report_bundle
from botping.bot.reports.period_parse import parse_period_line
from botping.bot.states import AddBotStates, QuietHoursStates, ReportStates, SettingStates
from botping.db import queries
from botping.db.pool import Database, generate_heartbeat_secret
from botping.heartbeat_server import HeartbeatServer
from botping.monitor.checker import check_getme

logger = logging.getLogger(__name__)


def setup_router() -> Router:
    router = Router()

    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
        if message.chat.type == "private":
            try:
                await refresh_chat_commands_and_menu(message.bot, message.chat.id)
            except Exception:
                logger.exception(
                    "Не удалось обновить команды/меню для chat_id=%s", message.chat.id
                )
            try:
                rm = await message.answer(".", reply_markup=ReplyKeyboardRemove())
                await rm.delete()
            except Exception:
                logger.exception("Не удалось снять reply-клавиатуру")
        text, markup = await screens.render_screen("main", db)
        await refresh_interface(
            message.bot,
            message.chat.id,
            state,
            db,
            text,
            reply_markup=markup,
        )

    @router.message(Command("status"))
    async def cmd_status(
        message: Message,
        state: FSMContext,
        db: Database,
        hb_server: HeartbeatServer | None = None,
    ) -> None:
        await screens.goto_screen_message(
            message, state, db, "status", hb_server=hb_server
        )

    @router.message(Command("failures"))
    async def cmd_failures(
        message: Message,
        state: FSMContext,
        command: CommandObject,
        db: Database,
    ) -> None:
        await screens.goto_screen_message(
            message,
            state,
            db,
            "failures",
            failures_args=command.args,
        )

    @router.message(Command("settings"))
    async def cmd_settings(message: Message, state: FSMContext, db: Database) -> None:
        await screens.goto_screen_message(message, state, db, "settings")

    @router.message(Command("report"))
    async def cmd_report(message: Message, state: FSMContext, db: Database) -> None:
        await state.set_state(ReportStates.waiting_period)
        await screens.goto_screen_message(message, state, db, "report_prompt", push=True)

    @router.callback_query(F.data == "nav:back")
    async def on_nav_back(
        cq: CallbackQuery,
        state: FSMContext,
        db: Database,
        hb_server: HeartbeatServer | None = None,
    ) -> None:
        await state.set_state(None)
        prev = await pop_nav(state)
        data = await state.get_data()
        failures_args = data.get("failures_args")
        await screens.goto_screen_cq(
            cq,
            state,
            db,
            prev,
            push=False,
            hb_server=hb_server,
            failures_args=str(failures_args) if failures_args else None,
        )
        await cq.answer()

    @router.callback_query(F.data == "menu:main")
    async def on_menu_main(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.clear()
        await screens.goto_screen_cq(cq, state, db, "main", push=False)
        await cq.answer()

    @router.callback_query(F.data == "menu:help")
    async def on_menu_help(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await screens.goto_screen_cq(cq, state, db, "help")
        await cq.answer()

    @router.callback_query(F.data == "menu:status")
    async def on_menu_status(
        cq: CallbackQuery,
        state: FSMContext,
        db: Database,
        hb_server: HeartbeatServer | None = None,
    ) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "status", hb_server=hb_server)
        await cq.answer()

    @router.callback_query(F.data == "menu:failures")
    async def on_menu_failures(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "failures")
        await cq.answer()

    @router.callback_query(F.data == "menu:settings")
    async def on_menu_settings(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "settings")
        await cq.answer()

    @router.callback_query(F.data == "menu:bots")
    async def on_menu_bots(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "bots")
        await cq.answer()

    @router.callback_query(F.data == "menu:disk")
    async def on_menu_disk(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "disk")
        await cq.answer()

    @router.callback_query(F.data == "menu:report_excel")
    async def on_menu_report_excel(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(ReportStates.waiting_period)
        await screens.goto_screen_cq(cq, state, db, "report_prompt")
        await cq.answer()

    @router.callback_query(F.data == "report:cancel")
    async def on_report_cancel(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "main", push=False)
        await cq.answer()

    @router.message(ReportStates.waiting_period, F.text)
    async def on_report_period(message: Message, state: FSMContext, db: Database) -> None:
        try:
            start, end = parse_period_line(message.text or "")
        except ValueError as e:
            await render_panel_message(
                message,
                state,
                db,
                f"⚠️ {e}\n\nПовторите ввод периода.",
                reply_markup=kb.report_cancel_keyboard(),
            )
            return
        await render_panel_message(
            message, state, db, "⏳ Собираю данные и формирую Excel…",
            reply_markup=kb.report_cancel_keyboard(),
        )
        try:
            bundle = await build_availability_report_bundle(db, start, end)
            await send_reference_document(
                message.bot,
                message.chat.id,
                BufferedInputFile(bundle.blob, filename=bundle.filename),
                caption=bundle.caption,
            )
        except Exception as e:
            logger.exception("excel report failed")
            hint = str(e).strip()
            if len(hint) > 280:
                hint = hint[:277] + "…"
            await render_panel_message(
                message,
                state,
                db,
                f"❌ Не удалось сформировать файл.\n{hint}",
                reply_markup=kb.report_cancel_keyboard(),
            )
            return
        await state.set_state(None)
        await screens.goto_screen_message(message, state, db, "main", push=False)

    @router.callback_query(F.data.startswith("set:"))
    async def on_set_click(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        key = cq.data.split(":", 1)[1]
        if key not in META:
            await cq.answer("❌ Неизвестный параметр.", show_alert=True)
            return
        await state.update_data(set_key=key)
        if key == "quiet_hours":
            await state.set_state(QuietHoursStates.waiting_json)
        else:
            await state.set_state(SettingStates.waiting_value)
        await screens.goto_screen_cq(cq, state, db, f"setting:{key}")
        await cq.answer()

    @router.message(SettingStates.waiting_value, F.text)
    async def on_setting_value(message: Message, state: FSMContext, db: Database) -> None:
        data = await state.get_data()
        key = str(data.get("set_key") or "")
        if not key:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "settings", push=False, pop=1)
            return
        raw = (message.text or "").strip()
        if not re.fullmatch(r"-?\d+", raw):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Нужно целое число. Повторите ввод.",
                reply_markup=kb.setting_input_keyboard(),
            )
            return
        iv = int(raw)
        if key in ("daily_excel_report_enabled", "telegram_api_probe_enabled") and iv not in (
            0,
            1,
        ):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Допустимо только 0 (выкл) или 1 (вкл).",
                reply_markup=kb.setting_input_keyboard(),
            )
            return
        uid = message.from_user.id if message.from_user else 0
        await queries.set_setting(db, key, str(iv), admin_chat_id=uid)
        await state.set_state(None)
        await render_panel_message(
            message,
            state,
            db,
            f"✅ Сохранено: <code>{key}</code> = {iv}",
            reply_markup=kb.settings_menu(),
        )

    @router.message(QuietHoursStates.waiting_json, F.text)
    async def on_quiet_json(message: Message, state: FSMContext, db: Database) -> None:
        raw = (message.text or "").strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Невалидный JSON. Повторите или отправьте {}",
                reply_markup=kb.setting_input_keyboard(),
            )
            return
        if obj != {} and not isinstance(obj, dict):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Нужен объект JSON или {}.",
                reply_markup=kb.setting_input_keyboard(),
            )
            return
        uid = message.from_user.id if message.from_user else 0
        await queries.set_setting(
            db, "quiet_hours", json.dumps(obj, ensure_ascii=False), admin_chat_id=uid
        )
        await state.set_state(None)
        await screens.goto_screen_message(message, state, db, "settings", push=False, pop=1)

    @router.callback_query(F.data.startswith("bot:view:"))
    async def on_bot_view(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        await screens.goto_screen_cq(cq, state, db, f"bot:{bid}")
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:snippet:"))
    async def on_bot_snippet(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        secret = str(b["heartbeat_secret"])
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            "📋 <b>Сниппет heartbeat</b>\n"
            "Вставьте в код бота рядом с <code>dp.start_polling</code>.",
        )
        for part in chunk_text(heartbeat_snippet(secret)):
            await send_reference(cq.bot, chat_id, f"<pre>{part}</pre>")
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:secret:"))
    async def on_bot_secret(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        secret = str(b["heartbeat_secret"])
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            f"🔑 Секрет <b>{b['display_name']}</b> (id={bid}):\n"
            f"<code>{secret}</code>\n\n"
            "<i>Не делитесь — возможны фальшивые пинги.</i>",
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:rotate:"))
    async def on_bot_rotate(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        new_secret = generate_heartbeat_secret()
        await queries.regenerate_heartbeat_secret(db, bid, new_secret)
        chat_id = cq.message.chat.id if cq.message else 0
        await send_reference(
            cq.bot,
            chat_id,
            f"🔄 Новый секрет для <b>{b['display_name']}</b>:\n"
            f"<code>{new_secret}</code>\n\n"
            "Обновите сниппет и перезапустите бота.",
        )
        await cq.answer("✅ Секрет обновлён")

    @router.callback_query(F.data.startswith("bot:toggle:"))
    async def on_bot_toggle(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await queries.update_bot_enabled(db, bid, not bool(b["enabled"]))
        await screens.goto_screen_cq(cq, state, db, f"bot:{bid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:delask:"))
    async def on_bot_delask(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await screens.goto_screen_cq(cq, state, db, f"bot_del:{bid}", push=False)
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:del:"))
    async def on_bot_del(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        await queries.delete_monitored_bot(db, bid)
        await screens.goto_screen_cq(cq, state, db, "bots", push=False, pop=1)
        await cq.answer("✅ Удалён")

    @router.callback_query(F.data == "bot:add")
    async def on_bot_add(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(AddBotStates.waiting_name)
        await screens.goto_screen_cq(cq, state, db, "add_bot_name")
        await cq.answer()

    @router.message(AddBotStates.waiting_name, F.text)
    async def on_add_name(message: Message, state: FSMContext, db: Database) -> None:
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
        await state.update_data(new_bot_name=name)
        await state.set_state(AddBotStates.waiting_token)
        await screens.goto_screen_message(
            message, state, db, "add_bot_token", push=False
        )

    @router.message(AddBotStates.waiting_token, F.text)
    async def on_add_token(message: Message, state: FSMContext, db: Database) -> None:
        token = (message.text or "").strip()
        data = await state.get_data()
        name = str(data.get("new_bot_name") or "").strip()
        if not token or not name:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "bots", push=False)
            return
        try:
            await message.delete()
        except Exception:
            pass
        timeout = (await queries.load_all_settings(db))["request_timeout_sec"]
        async with httpx.AsyncClient() as client:
            res = await check_getme(client, token, float(timeout))
        if not res.ok:
            await render_panel_message(
                message,
                state,
                db,
                f"❌ Токен невалиден: {res.error_text}",
                reply_markup=kb.cancel_input_keyboard(),
            )
            return
        secret = generate_heartbeat_secret()
        new_id = await queries.insert_monitored_bot(db, name, token, secret)
        await state.set_state(None)
        chat_id = message.chat.id
        await send_reference(
            message.bot,
            chat_id,
            f"📋 Сниппет для <b>{name}</b> (id={new_id}):",
        )
        for part in chunk_text(heartbeat_snippet(secret)):
            await send_reference(message.bot, chat_id, f"<pre>{part}</pre>")
        await screens.goto_screen_message(message, state, db, "main", push=False)

    @router.message(StateFilter(default_state), F.text, ~F.text.startswith("/"))
    async def fallback_plain(message: Message, state: FSMContext, db: Database) -> None:
        text, markup = await screens.render_screen("main", db)
        await render_panel_message(
            message,
            state,
            db,
            f"ℹ️ Напишите /start или выберите раздел.\n\n{text}",
            reply_markup=markup,
        )

    register_entity_settings_handlers(router)
    register_names_handlers(router)
    register_sites_handlers(router)
    register_websites_handlers(router)
    return router
