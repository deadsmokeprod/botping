from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot import panel_screens as screens
from botping.bot.panel import render_panel_message, set_current_screen
from botping.bot.states import RenameNameStates
from botping.db import queries
from botping.db.pool import Database

_NAME_MAX_LEN = 64
_VALID_KINDS = frozenset({"bot", "router", "target", "website", "module"})


async def _apply_display_name(
    db: Database, kind: str, entity_id: int, display_name: str
) -> None:
    if kind == "bot":
        await queries.update_bot_display_name(db, entity_id, display_name)
    elif kind == "router":
        await queries.update_router_display_name(db, entity_id, display_name)
    elif kind == "target":
        await queries.update_router_target_display_name(db, entity_id, display_name)
    elif kind == "website":
        await queries.update_website_display_name(db, entity_id, display_name)
    elif kind == "module":
        await queries.update_website_module_display_name(db, entity_id, display_name)
    else:
        raise ValueError(f"unknown kind: {kind}")


def register_names_handlers(router: Router) -> None:
    @router.callback_query(F.data == "names:menu")
    async def on_names_menu(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, "names_menu")
        await cq.answer()

    @router.callback_query(F.data.startswith("names:cat:"))
    async def on_names_cat(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        kind = cq.data.split(":", 2)[2]
        if kind not in _VALID_KINDS:
            await cq.answer("❌ Неизвестная категория", show_alert=True)
            return
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, f"names_list:{kind}")
        await cq.answer()

    @router.callback_query(F.data.startswith("names:edit:"))
    async def on_names_edit(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        parts = cq.data.split(":")
        if len(parts) < 4:
            await cq.answer("❌ Ошибка", show_alert=True)
            return
        kind = parts[2]
        if kind not in _VALID_KINDS:
            await cq.answer("❌ Неизвестная категория", show_alert=True)
            return
        eid = int(parts[3])
        current = await screens.get_entity_display_name(db, kind, eid)
        if current is None:
            await cq.answer("❌ Не найден", show_alert=True)
            return
        await state.update_data(
            rename_kind=kind,
            rename_id=eid,
            names_return_screen=f"names_list:{kind}",
        )
        await state.set_state(RenameNameStates.waiting_value)
        await screens.goto_screen_cq(cq, state, db, f"names_edit:{kind}:{eid}")
        await cq.answer()

    @router.callback_query(F.data == "names:cancel")
    async def on_names_cancel(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        data = await state.get_data()
        return_screen = str(data.get("names_return_screen") or "names_menu")
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, return_screen, push=False)
        await cq.answer()

    @router.message(RenameNameStates.waiting_value, F.text)
    async def on_rename_value(message: Message, state: FSMContext, db: Database) -> None:
        data = await state.get_data()
        kind = str(data.get("rename_kind") or "")
        entity_id = int(data.get("rename_id") or 0)
        return_screen = str(data.get("names_return_screen") or f"names_list:{kind}")

        if kind not in _VALID_KINDS or entity_id <= 0:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "names_menu", push=False)
            return

        name = (message.text or "").strip()
        if not name:
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Имя не может быть пустым.",
                reply_markup=kb.names_cancel_keyboard(),
            )
            return
        if len(name) > _NAME_MAX_LEN:
            await render_panel_message(
                message,
                state,
                db,
                f"⚠️ Не больше {_NAME_MAX_LEN} символов. Повторите ввод.",
                reply_markup=kb.names_cancel_keyboard(),
            )
            return

        current = await screens.get_entity_display_name(db, kind, entity_id)
        if current is None:
            await state.set_state(None)
            await screens.goto_screen_message(message, state, db, "names_menu", push=False)
            return

        await _apply_display_name(db, kind, entity_id, name)
        await state.set_state(None)
        await set_current_screen(state, return_screen)
        text, markup = await screens.render_screen(return_screen, db)
        await render_panel_message(
            message,
            state,
            db,
            f"✅ Сохранено: <b>{name}</b>\n\n{text}",
            reply_markup=markup,
        )
