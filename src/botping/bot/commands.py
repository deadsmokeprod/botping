from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
)

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="🔄 Обновить интерфейс"),
    BotCommand(command="status", description="📊 Статус ботов, роутеров и сайтов"),
    BotCommand(command="failures", description="⚠️ Сбои за 7 дней"),
    BotCommand(command="report", description="📈 Excel-отчёт"),
    BotCommand(command="settings", description="⚙️ Настройки"),
]

_MENU_BUTTON = MenuButtonCommands()


async def _set_commands_for_scope(bot: Bot, scope: object) -> None:
    await bot.set_my_commands(BOT_COMMANDS, scope=scope)
    await bot.set_my_commands(BOT_COMMANDS, scope=scope, language_code="ru")


async def _apply_commands(bot: Bot, chat_ids: Iterable[int]) -> None:
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
        await _set_commands_for_scope(bot, scope)
    for chat_id in chat_ids:
        await _set_commands_for_scope(bot, BotCommandScopeChat(chat_id=chat_id))


async def _apply_menu_button(bot: Bot, chat_ids: Iterable[int]) -> None:
    await bot.set_chat_menu_button(menu_button=_MENU_BUTTON)
    for chat_id in chat_ids:
        try:
            await bot.set_chat_menu_button(chat_id=chat_id, menu_button=_MENU_BUTTON)
        except Exception:
            logger.warning("set_chat_menu_button для chat_id=%s не удался", chat_id)


async def refresh_chat_commands_and_menu(bot: Bot, chat_id: int) -> None:
    """Команды и кнопка ☰ для конкретного личного чата (после /start)."""
    scope = BotCommandScopeChat(chat_id=chat_id)
    await _set_commands_for_scope(bot, scope)
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
            await _apply_commands(bot, ids)
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
