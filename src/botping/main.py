from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from botping.bot.handlers import setup_router
from botping.bot.middlewares import AdminChatMiddleware, DbMiddleware
from botping.config import load_settings
from botping.db.pool import Database, set_database
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

    async def notify(text: str) -> None:
        for chat_id in settings.admin_chat_ids:
            try:
                await bot.send_message(chat_id, text)
            except Exception:
                logger.exception("Failed to send alert to %s", chat_id)

    stop = asyncio.Event()
    sched_task, http_client = start_scheduler(db, notify, stop)
    daily_task = start_daily_report_loop(db, bot, settings.admin_chat_ids, stop)
    disk_task = start_disk_guard(db, notify, stop)

    # Только message/callback_query: на dp.update приходят my_chat_member и др. без from_user
    dp.message.outer_middleware(AdminChatMiddleware(settings))
    dp.message.outer_middleware(DbMiddleware(db))
    dp.callback_query.outer_middleware(AdminChatMiddleware(settings))
    dp.callback_query.outer_middleware(DbMiddleware(db))
    dp.include_router(setup_router())

    try:
        await dp.start_polling(bot)
    finally:
        stop.set()
        sched_task.cancel()
        daily_task.cancel()
        disk_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass
        try:
            await daily_task
        except asyncio.CancelledError:
            pass
        try:
            await disk_task
        except asyncio.CancelledError:
            pass
        await http_client.aclose()
        await db.close()


def run() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    run()
