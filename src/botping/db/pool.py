from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._apply_schema()
        import botping.db.queries as queries

        await queries.ensure_defaults(self._conn)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _apply_schema(self) -> None:
        assert self._conn is not None
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await self._conn.executescript(sql)
        await self._conn.commit()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self.connection.execute(sql, params)
            await self.connection.commit()
            return cur

    async def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        async with self._lock:
            await self.connection.executemany(sql, seq)
            await self.connection.commit()

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        async with self._lock:
            cur = await self.connection.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cur = await self.connection.execute(sql, params)
            return await cur.fetchall()

    async def write_returning_one(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> aiosqlite.Row | None:
        async with self._lock:
            cur = await self.connection.execute(sql, params)
            row = await cur.fetchone()
            await self.connection.commit()
            return row

    async def vacuum(self) -> None:
        async with self._lock:
            await self.connection.execute("VACUUM")

    @property
    def path(self) -> str:
        return self._path


_db: Database | None = None


def get_database() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


def set_database(db: Database) -> None:
    global _db
    _db = db
