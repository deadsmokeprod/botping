from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Обновить интерфейс"),
    BotCommand(command="status", description="Состояние ботов, Telegram API и диск"),
    BotCommand(command="failures", description="Инциденты за 7 дней (или даты)"),
    BotCommand(command="report", description="Excel-отчёт за период"),
    BotCommand(command="settings", description="Параметры мониторинга"),
]


async def register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
