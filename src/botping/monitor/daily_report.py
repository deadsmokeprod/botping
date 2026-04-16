from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from aiogram import Bot
from aiogram.types import BufferedInputFile

from botping.bot.reports.build import build_availability_report_bundle
from botping.db import queries
from botping.db.pool import Database
from botping.timeutil import moscow_yesterday_date, seconds_until_next_midnight_moscow

logger = logging.getLogger(__name__)

DAILY_REPORT_LAST_DATE_KEY = "daily_excel_report_last_date"


def _parse_last_sent(raw: str | None) -> date | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_to_send(last_sent: date | None, yesterday: date) -> list[date]:
    if last_sent is not None and last_sent > yesterday:
        return []
    if last_sent is None:
        return [yesterday]
    start = last_sent + timedelta(days=1)
    if start > yesterday:
        return []
    out: list[date] = []
    d = start
    while d <= yesterday:
        out.append(d)
        d += timedelta(days=1)
    return out


async def daily_report_loop(
    db: Database,
    bot: Bot,
    admin_chat_ids: tuple[int, ...],
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds_until_next_midnight_moscow())
            if stop.is_set():
                return
        except asyncio.TimeoutError:
            pass

        try:
            merged = await queries.load_all_settings(db)
            if not bool(merged.get("daily_excel_report_enabled")):
                continue

            yesterday = moscow_yesterday_date()
            last_raw = await queries.get_setting(db, DAILY_REPORT_LAST_DATE_KEY)
            last_sent = _parse_last_sent(last_raw)
            days = _days_to_send(last_sent, yesterday)
            if not days:
                continue

            for day in days:
                start = datetime.combine(day, time.min)
                end = datetime.combine(day, time(23, 59, 59))
                try:
                    bundle = await build_availability_report_bundle(db, start, end)
                    send_ok = True
                    for chat_id in admin_chat_ids:
                        try:
                            await bot.send_document(
                                chat_id,
                                BufferedInputFile(bundle.blob, filename=bundle.filename),
                                caption=bundle.caption,
                            )
                        except Exception:
                            send_ok = False
                            logger.exception(
                                "daily report: failed to send document to chat_id=%s", chat_id
                            )
                    if send_ok:
                        await queries.set_setting(
                            db,
                            DAILY_REPORT_LAST_DATE_KEY,
                            day.isoformat(),
                            admin_chat_id=None,
                        )
                    else:
                        roll = day - timedelta(days=1)
                        await queries.set_setting(
                            db,
                            DAILY_REPORT_LAST_DATE_KEY,
                            roll.isoformat(),
                            admin_chat_id=None,
                        )
                        break
                except Exception:
                    logger.exception("daily report failed for data day %s", day.isoformat())
                    roll = day - timedelta(days=1)
                    await queries.set_setting(
                        db,
                        DAILY_REPORT_LAST_DATE_KEY,
                        roll.isoformat(),
                        admin_chat_id=None,
                    )
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily report tick error")


def start_daily_report_loop(
    db: Database,
    bot: Bot,
    admin_chat_ids: tuple[int, ...],
    stop: asyncio.Event,
) -> asyncio.Task[None]:
    return asyncio.create_task(
        daily_report_loop(db, bot, admin_chat_ids, stop),
        name="botping-daily-report",
    )
