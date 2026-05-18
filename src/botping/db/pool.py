from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def generate_heartbeat_secret() -> str:
    """URL-safe 24-byte token (~32 символа)."""
    return secrets.token_urlsafe(24)


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
        await self._migrate()

    async def _migrate(self) -> None:
        """Идемпотентные миграции поверх schema.sql для уже существующих БД."""
        assert self._conn is not None
        cur = await self._conn.execute("PRAGMA table_info(checks)")
        cols = [row[1] for row in await cur.fetchall()]
        if "check_type" not in cols:
            await self._conn.execute(
                "ALTER TABLE checks ADD COLUMN check_type TEXT NOT NULL DEFAULT 'getme'"
            )
            await self._conn.commit()

        cur = await self._conn.execute("PRAGMA table_info(monitored_bots)")
        mb_cols = [row[1] for row in await cur.fetchall()]
        if "heartbeat_secret" not in mb_cols:
            await self._conn.execute(
                "ALTER TABLE monitored_bots ADD COLUMN heartbeat_secret TEXT"
            )
        if "last_heartbeat_at" not in mb_cols:
            await self._conn.execute(
                "ALTER TABLE monitored_bots ADD COLUMN last_heartbeat_at TEXT"
            )
        if "last_heartbeat_ip" not in mb_cols:
            await self._conn.execute(
                "ALTER TABLE monitored_bots ADD COLUMN last_heartbeat_ip TEXT"
            )
        await self._conn.commit()

        cur = await self._conn.execute(
            "SELECT id FROM monitored_bots WHERE heartbeat_secret IS NULL OR heartbeat_secret = ''"
        )
        missing = [int(r[0]) for r in await cur.fetchall()]
        for bid in missing:
            await self._conn.execute(
                "UPDATE monitored_bots SET heartbeat_secret = ? WHERE id = ?",
                (generate_heartbeat_secret(), bid),
            )
        if missing:
            await self._conn.commit()

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS monitored_routers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                heartbeat_secret TEXT NOT NULL UNIQUE,
                last_heartbeat_at TEXT,
                last_heartbeat_ip TEXT
            );
            CREATE TABLE IF NOT EXISTS router_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                router_id INTEGER NOT NULL REFERENCES monitored_routers(id) ON DELETE CASCADE,
                display_name TEXT NOT NULL,
                address TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_ok_at TEXT,
                last_latency_ms INTEGER,
                last_error TEXT,
                UNIQUE(router_id, address)
            );
            CREATE INDEX IF NOT EXISTS idx_router_targets_router ON router_targets(router_id);
            CREATE TABLE IF NOT EXISTS router_target_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL REFERENCES router_targets(id) ON DELETE CASCADE,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                ok INTEGER NOT NULL,
                latency_ms INTEGER,
                error_text TEXT,
                check_type TEXT NOT NULL DEFAULT 'lan_push'
            );
            CREATE INDEX IF NOT EXISTS idx_router_target_checks_target_ts ON router_target_checks(target_id, ts);
            CREATE TABLE IF NOT EXISTS router_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                router_id INTEGER NOT NULL REFERENCES monitored_routers(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                last_error TEXT,
                last_alert_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_router_incidents_open ON router_incidents(router_id) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS router_target_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL REFERENCES router_targets(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                last_error TEXT,
                last_alert_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_router_target_incidents_open ON router_target_incidents(target_id) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS router_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                router_id INTEGER NOT NULL REFERENCES monitored_routers(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                source_ip TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_router_events_router_ts ON router_events(router_id, created_at);
            """
        )
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
