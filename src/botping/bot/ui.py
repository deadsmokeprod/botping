from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InaccessibleMessage

logger = logging.getLogger(__name__)


async def edit_or_answer(
    cq: CallbackQuery,
    text: str,
    *,
    reply_markup: Any = None,
) -> None:
    """Редактирует сообщение колбэка; при ошибке Telegram — шлёт новое в тот же чат."""
    m = cq.message
    if m is None or isinstance(m, InaccessibleMessage):
        await cq.answer("Сообщение нельзя изменить. Откройте /start заново.", show_alert=True)
        return
    try:
        await m.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        logger.warning("edit_text: %s — отправляю новым сообщением", e)
        await m.answer(text, reply_markup=reply_markup)
