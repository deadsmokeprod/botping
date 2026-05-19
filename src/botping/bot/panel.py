from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from botping.db import queries
from botping.db.pool import Database

logger = logging.getLogger(__name__)

KEY_PANEL_MESSAGE_ID = "panel_message_id"
KEY_PANEL_IDS = "panel_ids"
KEY_NAV_STACK = "nav_stack"
KEY_CURRENT_SCREEN = "current_screen"  # same key used in panel_screens.goto_screen_*


def merge_panel_ids(*groups: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for group in groups:
        for mid in group:
            if mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


async def load_panel_ids(state: FSMContext, db: Database, chat_id: int) -> list[int]:
    data = await state.get_data()
    fsm_ids = list(data.get(KEY_PANEL_IDS) or [])
    _db_mid, db_ids = await queries.load_admin_chat_ui(db, chat_id)
    return merge_panel_ids(fsm_ids, db_ids)


async def _persist_panel(
    state: FSMContext,
    db: Database,
    chat_id: int,
    panel_message_id: int | None,
    panel_ids: list[int],
) -> None:
    await state.update_data(
        **{
            KEY_PANEL_MESSAGE_ID: panel_message_id,
            KEY_PANEL_IDS: panel_ids,
        }
    )
    await queries.save_admin_chat_ui(
        db,
        chat_id,
        panel_message_id=panel_message_id,
        panel_ids=panel_ids,
    )


async def track_panel_id(state: FSMContext, db: Database, chat_id: int, message_id: int) -> None:
    data = await state.get_data()
    ids = list(data.get(KEY_PANEL_IDS) or [])
    if message_id not in ids:
        ids.append(message_id)
    panel_mid = data.get(KEY_PANEL_MESSAGE_ID)
    await _persist_panel(
        state,
        db,
        chat_id,
        int(panel_mid) if panel_mid is not None else message_id,
        ids,
    )


async def push_nav(state: FSMContext, new_screen_key: str) -> None:
    """Сохраняет текущий экран в стек и переключает current на new_screen_key."""
    data = await state.get_data()
    stack: list[str] = list(data.get(KEY_NAV_STACK) or [])
    current = str(data.get(KEY_CURRENT_SCREEN) or "main")
    if current != new_screen_key:
        stack.append(current)
    await state.update_data(
        **{KEY_NAV_STACK: stack[-20:], KEY_CURRENT_SCREEN: new_screen_key}
    )


async def pop_nav(state: FSMContext) -> str:
    data = await state.get_data()
    stack: list[str] = list(data.get(KEY_NAV_STACK) or [])
    if stack:
        prev = stack.pop()
        await state.update_data(**{KEY_NAV_STACK: stack, KEY_CURRENT_SCREEN: prev})
        return prev
    await state.update_data(**{KEY_NAV_STACK: [], KEY_CURRENT_SCREEN: "main"})
    return "main"


async def set_current_screen(state: FSMContext, screen_key: str) -> None:
    await state.update_data(**{KEY_CURRENT_SCREEN: screen_key})


async def delete_panel_messages(bot: Bot, chat_id: int, panel_ids: list[int]) -> None:
    for mid in panel_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except TelegramBadRequest:
            pass
        except Exception:
            logger.debug("delete_message failed chat=%s msg=%s", chat_id, mid, exc_info=True)


async def render_panel(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    db: Database,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
) -> int:
    data = await state.get_data()
    panel_mid = data.get(KEY_PANEL_MESSAGE_ID)
    if panel_mid is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=int(panel_mid),
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            await track_panel_id(state, db, chat_id, int(panel_mid))
            return int(panel_mid)
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return int(panel_mid)
            logger.warning("edit_message_text failed: %s", e)

    msg = await bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    await track_panel_id(state, db, chat_id, msg.message_id)
    return msg.message_id


async def render_panel_cq(
    cq: CallbackQuery,
    state: FSMContext,
    db: Database,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
) -> None:
    if cq.message is None or isinstance(cq.message, InaccessibleMessage):
        return
    chat_id = cq.message.chat.id
    await render_panel(
        cq.bot,
        chat_id,
        state,
        db,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def render_panel_message(
    message: Message,
    state: FSMContext,
    db: Database,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
) -> int:
    return await render_panel(
        message.bot,
        message.chat.id,
        state,
        db,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def send_reference(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    **kwargs: Any,
) -> Message:
    """Справочное сообщение — не входит в panel_ids и не удаляется при /start."""
    return await bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)


async def send_reference_document(
    bot: Bot,
    chat_id: int,
    document: Any,
    *,
    caption: str | None = None,
) -> Message:
    return await bot.send_document(
        chat_id, document, caption=caption, parse_mode="HTML"
    )


async def refresh_interface(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    db: Database,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
) -> int:
    panel_ids = await load_panel_ids(state, db, chat_id)
    await delete_panel_messages(bot, chat_id, panel_ids)
    await state.clear()
    msg = await bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    await _persist_panel(state, db, chat_id, msg.message_id, [msg.message_id])
    await state.update_data(**{KEY_CURRENT_SCREEN: "main", KEY_NAV_STACK: []})
    return msg.message_id
