from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from botping.db import queries
from botping.db.pool import Database

logger = logging.getLogger(__name__)


def _client_ip(request: web.Request) -> str:
    """Лучший доступный IP источника запроса."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    peer = request.transport.get_extra_info("peername") if request.transport else None
    if peer:
        return str(peer[0])
    return "-"


def _extract_secret(request: web.Request) -> str:
    secret = request.headers.get("X-Heartbeat-Secret", "").strip()
    if secret:
        return secret
    # fallback: query param ?secret=... (на крайний случай)
    return (request.query.get("secret") or "").strip()


async def _handle_heartbeat(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    secret = _extract_secret(request)
    if not secret:
        return web.json_response({"ok": False, "error": "missing_secret"}, status=401)

    bot = await queries.find_bot_by_heartbeat_secret(db, secret)
    if bot is None:
        logger.warning("Heartbeat with unknown secret from %s", _client_ip(request))
        return web.json_response({"ok": False, "error": "unknown_secret"}, status=401)

    await queries.touch_heartbeat(db, int(bot["id"]), _client_ip(request))
    return web.json_response({"ok": True, "bot_id": int(bot["id"])})


async def _handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "botping-heartbeat"})


def _build_app(db: Database) -> web.Application:
    app = web.Application()
    app["db"] = db
    app.router.add_post("/heartbeat", _handle_heartbeat)
    app.router.add_get("/heartbeat", _handle_heartbeat)
    app.router.add_get("/health", _handle_health)
    app.router.add_get("/", _handle_health)
    return app


class HeartbeatServer:
    def __init__(self, db: Database, port: int) -> None:
        self._db = db
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = _build_app(self._db)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port)
        await site.start()
        logger.info("Heartbeat server listening on 0.0.0.0:%s", self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


def start_heartbeat_server(db: Database, port: int) -> tuple[HeartbeatServer, asyncio.Task[Any]]:
    server = HeartbeatServer(db, port)

    async def _run() -> None:
        try:
            await server.start()
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await server.stop()
            raise

    task = asyncio.create_task(_run(), name="botping-heartbeat-server")
    return server, task
