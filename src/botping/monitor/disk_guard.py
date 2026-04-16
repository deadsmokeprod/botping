from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from botping.db import queries
from botping.db.pool import Database

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]

DELETE_BATCH = 10_000
HYSTERESIS_PCT = 10


@dataclass(frozen=True)
class DiskInfo:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_pct: float
    db_size_bytes: int

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024 ** 3)

    @property
    def used_gb(self) -> float:
        return self.used_bytes / (1024 ** 3)

    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024 ** 3)

    @property
    def db_size_mb(self) -> float:
        return self.db_size_bytes / (1024 ** 2)


def get_disk_info(db_path: str) -> DiskInfo:
    resolved = Path(db_path).resolve()
    usage = shutil.disk_usage(resolved.parent)
    try:
        db_size = os.path.getsize(resolved)
    except OSError:
        db_size = 0
    return DiskInfo(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        used_pct=usage.used / usage.total * 100 if usage.total else 0,
        db_size_bytes=db_size,
    )


async def _run_cleanup(
    db: Database,
    notify: NotifyFn,
    threshold: int,
) -> None:
    target = threshold - HYSTERESIS_PCT
    info = get_disk_info(db.path)
    total_deleted_checks = 0
    total_deleted_incidents = 0
    total_deleted_audit = 0

    await notify(
        f"Диск заполнен на {info.used_pct:.1f}% "
        f"({info.used_gb:.1f} / {info.total_gb:.1f} ГБ). "
        f"Порог {threshold}% — запускаю очистку старых данных..."
    )

    while info.used_pct > target:
        deleted = await queries.delete_oldest_checks(db, DELETE_BATCH)
        total_deleted_checks += deleted
        if deleted == 0:
            break
        await asyncio.sleep(0.1)
        info = get_disk_info(db.path)

    if info.used_pct > target:
        while info.used_pct > target:
            deleted = await queries.delete_oldest_incidents_closed(db, DELETE_BATCH)
            total_deleted_incidents += deleted
            if deleted == 0:
                break
            await asyncio.sleep(0.1)
            info = get_disk_info(db.path)

    if info.used_pct > target:
        while info.used_pct > target:
            deleted = await queries.delete_oldest_audit(db, DELETE_BATCH)
            total_deleted_audit += deleted
            if deleted == 0:
                break
            await asyncio.sleep(0.1)
            info = get_disk_info(db.path)

    any_deleted = total_deleted_checks + total_deleted_incidents + total_deleted_audit
    if any_deleted > 0:
        try:
            await db.vacuum()
        except Exception:
            logger.exception("VACUUM failed after cleanup")

    info = get_disk_info(db.path)
    parts = []
    if total_deleted_checks:
        parts.append(f"проверок: {total_deleted_checks}")
    if total_deleted_incidents:
        parts.append(f"инцидентов: {total_deleted_incidents}")
    if total_deleted_audit:
        parts.append(f"аудита: {total_deleted_audit}")

    if parts:
        await notify(
            f"Очистка завершена. Удалено: {', '.join(parts)}. "
            f"Диск: {info.used_pct:.1f}% ({info.free_gb:.1f} ГБ свободно)."
        )
    else:
        await notify(
            f"Нечего удалять (таблицы пусты), но диск всё ещё заполнен "
            f"на {info.used_pct:.1f}%. Проверьте вручную — место занимает "
            f"что-то за пределами БД бота."
        )


async def disk_guard_loop(
    db: Database,
    notify: NotifyFn,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            settings = await queries.load_all_settings(db)
            interval = int(settings["disk_check_interval_sec"])
            threshold = int(settings["disk_usage_threshold_pct"])

            info = get_disk_info(db.path)
            logger.debug(
                "Disk check: %.1f%% used (%.1f/%.1f GB), db=%.1f MB",
                info.used_pct, info.used_gb, info.total_gb, info.db_size_mb,
            )

            if info.used_pct >= threshold:
                await _run_cleanup(db, notify, threshold)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("disk guard loop error")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def start_disk_guard(
    db: Database,
    notify: NotifyFn,
    stop: asyncio.Event,
) -> asyncio.Task[None]:
    return asyncio.create_task(
        disk_guard_loop(db, notify, stop),
        name="botping-disk-guard",
    )
