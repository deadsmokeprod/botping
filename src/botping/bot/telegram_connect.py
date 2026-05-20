from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger(__name__)


async def wait_for_telegram_api(
    bot: Bot,
    *,
    attempts: int = 20,
    max_delay_sec: int = 60,
) -> bool:
    """Дождаться доступности api.telegram.org (getMe). Не роняет процесс."""
    for n in range(1, attempts + 1):
        try:
            me = await bot.get_me()
            logger.info("Telegram API доступен (@%s, id=%s)", me.username, me.id)
            return True
        except (TelegramNetworkError, OSError, TimeoutError) as e:
            if n >= attempts:
                logger.error(
                    "Telegram API недоступен после %d попыток: %s",
                    attempts,
                    e,
                )
                return False
            delay = min(2**n, max_delay_sec)
            logger.warning(
                "Telegram API недоступен (%s), повтор %d/%d через %d с",
                e,
                n,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
        except Exception:
            logger.exception("Неожиданная ошибка getMe, попытка %d/%d", n, attempts)
            if n >= attempts:
                return False
            await asyncio.sleep(min(2**n, max_delay_sec))
    return False
