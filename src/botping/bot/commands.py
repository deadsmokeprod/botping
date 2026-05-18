from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    MenuButtonCommands,
)

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Обновить интерфейс"),
    BotCommand(command="status", description="Состояние ботов, Telegram API и диск"),
    BotCommand(command="failures", description="Инциденты за 7 дней (или даты)"),
    BotCommand(command="report", description="Excel-отчёт за период"),
    BotCommand(command="settings", description="Параметры мониторинга"),
]

_MENU_BUTTON = MenuButtonCommands()


async def _apply_commands(bot: Bot) -> None:
    scopes = (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats())
    for scope in scopes:
        await bot.set_my_commands(BOT_COMMANDS, scope=scope)
        await bot.set_my_commands(BOT_COMMANDS, scope=scope, language_code="ru")


async def _apply_menu_button(bot: Bot, chat_ids: Iterable[int]) -> None:
    await bot.set_chat_menu_button(menu_button=_MENU_BUTTON)
    for chat_id in chat_ids:
        try:
            await bot.set_chat_menu_button(chat_id=chat_id, menu_button=_MENU_BUTTON)
        except Exception:
            logger.warning("set_chat_menu_button для chat_id=%s не удался", chat_id)


async def refresh_chat_menu_button(bot: Bot, chat_id: int) -> None:
    """Кнопка «меню» слева от поля ввода в личном чате с ботом."""
    await bot.set_chat_menu_button(chat_id=chat_id, menu_button=_MENU_BUTTON)


async def register_bot_commands(
    bot: Bot,
    *,
    admin_chat_ids: Iterable[int] = (),
    attempts: int = 5,
) -> bool:
    ids = list(admin_chat_ids)
    for n in range(1, attempts + 1):
        try:
            await _apply_commands(bot)
            await _apply_menu_button(bot, ids)
            return True
        except Exception:
            if n >= attempts:
                logger.exception(
                    "Не удалось зарегистрировать команды/меню в Telegram после %d попыток",
                    attempts,
                )
                return False
            delay = min(2**n, 30)
            logger.warning(
                "register_bot_commands: попытка %d/%d не удалась, повтор через %d с",
                n,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    return False
