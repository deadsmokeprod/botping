from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from aiogram.exceptions import TelegramNetworkError

from botping.bot.commands import BOT_COMMANDS, register_bot_commands
from botping.bot.handlers import setup_router
from botping.bot.middlewares import AdminChatMiddleware, DbMiddleware
from botping.bot.telegram_connect import wait_for_telegram_api
from botping.config import load_settings
from botping.db import queries
from botping.db.pool import Database, set_database
from botping.heartbeat_server import start_heartbeat_server
from botping.monitor.daily_report import start_daily_report_loop
from botping.monitor.disk_guard import start_disk_guard
from botping.monitor.scheduler import start_scheduler

logger = logging.getLogger(__name__)


async def _amain() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.database_path)
    await db.connect()
    set_database(db)

    logger.info("Загружено admin_chat_ids: %s шт.", len(settings.admin_chat_ids))

    bot = Bot(settings.admin_bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    async def _register_commands_background() -> None:
        if await register_bot_commands(bot, admin_chat_ids=settings.admin_chat_ids):
            logger.info(
                "Команды Telegram зарегистрированы (%d шт., menu button для %d чатов)",
                len(BOT_COMMANDS),
                len(settings.admin_chat_ids),
            )
        else:
            logger.warning(
                "Меню команд не зарегистрировано (сеть/Telegram); /start всё равно работает"
            )

    async def notify(text: str) -> None:
        for chat_id in settings.admin_chat_ids:
            try:
                await bot.send_message(chat_id, text)
            except Exception:
                logger.exception("Failed to send alert to %s", chat_id)

    stop = asyncio.Event()
    sched_task, http_client = start_scheduler(db, notify, stop, settings.admin_bot_token)
    daily_task = start_daily_report_loop(db, bot, settings.admin_chat_ids, stop)
    disk_task = start_disk_guard(db, notify, stop)

    hb_port = (await queries.load_all_settings(db))["heartbeat_port"]
    hb_server, hb_task = start_heartbeat_server(db, port=int(hb_port), notify=notify)

    # Только message/callback_query: на dp.update приходят my_chat_member и др. без from_user
    dp.message.outer_middleware(AdminChatMiddleware(settings))
    dp.message.outer_middleware(DbMiddleware(db))
    dp.callback_query.outer_middleware(AdminChatMiddleware(settings))
    dp.callback_query.outer_middleware(DbMiddleware(db))
    dp.include_router(setup_router())

    cmd_task = asyncio.create_task(_register_commands_background(), name="register-commands")

    poll_retry_sec = 30
    try:
        while True:
            if not await wait_for_telegram_api(bot):
                logger.error(
                    "Ожидание Telegram API, повтор через %d с (бот не падает)",
                    poll_retry_sec,
                )
                await asyncio.sleep(poll_retry_sec)
                continue
            try:
                await dp.start_polling(bot, hb_server=hb_server)
                break
            except TelegramNetworkError as e:
                logger.error(
                    "Polling оборван (%s), переподключение через %d с",
                    e,
                    poll_retry_sec,
                )
                await asyncio.sleep(poll_retry_sec)
    finally:
        cmd_task.cancel()
        try:
            await cmd_task
        except asyncio.CancelledError:
            pass
        stop.set()
        sched_task.cancel()
        daily_task.cancel()
        disk_task.cancel()
        hb_task.cancel()
        for t in (sched_task, daily_task, disk_task, hb_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        try:
            await hb_server.stop()
        except Exception:
            logger.exception("heartbeat server stop failed")
        await http_client.aclose()
        await db.close()


def run() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    run()
