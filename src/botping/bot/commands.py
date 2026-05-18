from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Обновить интерфейс"),
    BotCommand(command="status", description="Состояние ботов, Telegram API и диск"),
    BotCommand(command="failures", description="Инциденты за 7 дней (или даты)"),
    BotCommand(command="report", description="Excel-отчёт за период"),
    BotCommand(command="settings", description="Параметры мониторинга"),
]


async def register_bot_commands(bot: Bot, *, attempts: int = 5) -> bool:
    for n in range(1, attempts + 1):
        try:
            await bot.set_my_commands(BOT_COMMANDS)
            return True
        except Exception:
            if n >= attempts:
                logger.exception(
                    "Не удалось зарегистрировать команды в Telegram после %d попыток",
                    attempts,
                )
                return False
            delay = min(2**n, 30)
            logger.warning(
                "set_my_commands: попытка %d/%d не удалась, повтор через %d с",
                n,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    return False
