from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from botping.config import Settings
from botping.db.pool import Database

logger = logging.getLogger(__name__)


class DbMiddleware(BaseMiddleware):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["db"] = self._db
        return await handler(event, data)


class AdminChatMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self._allowed = set(settings.admin_chat_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        uid: int | None = None
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            uid = event.from_user.id

        if uid is None:
            logger.debug("Апдейт без from_user — пропуск (не личное сообщение админа)")
            return None

        if uid not in self._allowed:
            logger.warning(
                "Доступ отклонён: user_id=%s (в ADMIN_CHAT_IDS записей: %s)",
                uid,
                len(self._allowed),
            )
            try:
                if isinstance(event, Message):
                    await event.answer(
                        "Доступ запрещён: ваш Telegram id не в ADMIN_CHAT_IDS.\n"
                        "Узнайте id (например @userinfobot) и добавьте в .env, перезапустите процесс."
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer("Нет доступа: id не в ADMIN_CHAT_IDS.", show_alert=True)
            except Exception:
                logger.exception("Не удалось отправить ответ об отказе в доступе")
            return None
        return await handler(event, data)
