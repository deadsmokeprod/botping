from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from aiohttp import web

from botping.db import queries
from botping.db.pool import Database
from botping.heartbeat_payload import parse_heartbeat_payload

logger = logging.getLogger(__name__)

_VALID_HEARTBEAT_RPM = 300  # запас 4x от нормального (1 бот = 2/мин)
_MIN_SECRET_LEN = 16
_MAX_HEARTBEAT_BODY = 16384
_RECENT_SUCCESS_TTL_SEC = 3600
_SECRETS_CACHE_TTL_SEC = 60
_SETTINGS_CACHE_TTL_SEC = 30
_UNAUTH_WINDOW_SEC = 60
_GC_INTERVAL_SEC = 300


def _trust_proxy() -> bool:
    return os.getenv("BOTPING_TRUST_PROXY", "0").strip() in ("1", "true", "True")


def _client_ip(request: web.Request) -> str:
    if _trust_proxy():
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip() or "-"
    peer = request.transport.get_extra_info("peername") if request.transport else None
    if peer:
        return str(peer[0])
    return "-"


def _extract_secret(request: web.Request) -> str:
    secret = request.headers.get("X-Heartbeat-Secret", "").strip()
    if secret:
        return secret
    return (request.query.get("secret") or "").strip()


NotifyFn = Callable[[str], Awaitable[None]]


class HeartbeatServer:
    def __init__(
        self, db: Database, port: int, notify: NotifyFn | None = None
    ) -> None:
        self._db = db
        self._port = port
        self._notify = notify
        self._runner: web.AppRunner | None = None
        self._gc_task: asyncio.Task[None] | None = None

        self._unauth_hits: dict[str, deque[float]] = {}
        self._fails: dict[str, deque[float]] = {}
        self._bans: dict[str, float] = {}
        self._recent_success: dict[str, float] = {}
        self._blocked_24h: deque[float] = deque()

        self._secrets_cache: list[tuple[str, int, str]] = []
        self._secrets_cached_at: float = 0.0

        self._settings_cache: dict[str, Any] = {}
        self._settings_cached_at: float = 0.0

    # ---- стата для /status ----

    def stats_snapshot(self) -> dict[str, int]:
        now = time.time()
        active_bans = sum(1 for t in self._bans.values() if t > now)
        cutoff = now - 86400
        while self._blocked_24h and self._blocked_24h[0] < cutoff:
            self._blocked_24h.popleft()
        return {"active_bans": active_bans, "blocked_24h": len(self._blocked_24h)}

    # ---- кэшированный доступ к настройкам ----

    async def _get_settings(self) -> dict[str, Any]:
        now = time.time()
        if now - self._settings_cached_at > _SETTINGS_CACHE_TTL_SEC or not self._settings_cache:
            self._settings_cache = await queries.load_all_settings(self._db)
            self._settings_cached_at = now
        return self._settings_cache

    async def _get_secrets(self) -> list[tuple[str, int, str]]:
        now = time.time()
        if now - self._secrets_cached_at > _SECRETS_CACHE_TTL_SEC:
            self._secrets_cache = await queries.list_heartbeat_secrets_for_cache(self._db)
            self._secrets_cached_at = now
        return self._secrets_cache

    def invalidate_secrets_cache(self) -> None:
        self._secrets_cached_at = 0.0

    # ---- учёт попыток и бан ----

    def _register_unauth(self, ip: str) -> int:
        now = time.time()
        dq = self._unauth_hits.setdefault(ip, deque())
        cutoff = now - _UNAUTH_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()
        dq.append(now)
        return len(dq)

    def _is_banned(self, ip: str) -> bool:
        ban_until = self._bans.get(ip)
        if ban_until is None:
            return False
        if time.time() < ban_until:
            return True
        self._bans.pop(ip, None)
        return False

    def _note_block(self) -> None:
        self._blocked_24h.append(time.time())

    def _has_recent_success(self, ip: str) -> bool:
        ts = self._recent_success.get(ip)
        if ts is None:
            return False
        if time.time() - ts > _RECENT_SUCCESS_TTL_SEC:
            self._recent_success.pop(ip, None)
            return False
        return True

    def _register_fail_maybe_ban(
        self, ip: str, fails_threshold: int, duration_sec: int
    ) -> bool:
        """Учитывает неуспех; возвращает True, если IP забанен."""
        now = time.time()
        dq = self._fails.setdefault(ip, deque())
        cutoff = now - duration_sec
        while dq and dq[0] < cutoff:
            dq.popleft()
        dq.append(now)
        if len(dq) >= fails_threshold:
            self._bans[ip] = now + duration_sec
            return True
        return False

    # ---- обработчики ----

    async def _handle_heartbeat(self, request: web.Request) -> web.Response:
        ip = _client_ip(request)

        if self._is_banned(ip):
            self._note_block()
            return web.Response(status=404)

        settings = await self._get_settings()
        unauth_rpm = int(settings["heartbeat_unauth_rate_per_min"])
        fails_threshold = int(settings["heartbeat_ban_fails_threshold"])
        ban_duration_sec = int(settings["heartbeat_ban_duration_min"]) * 60

        secret = _extract_secret(request)
        if not secret or len(secret) < _MIN_SECRET_LEN:
            hits = self._register_unauth(ip)
            if hits > unauth_rpm:
                self._note_block()
                return web.Response(status=404)
            trusted = self._has_recent_success(ip)
            banned = False
            if not trusted:
                banned = self._register_fail_maybe_ban(ip, fails_threshold, ban_duration_sec)
            if banned:
                logger.warning("heartbeat ban ip=%s reason=short_or_empty_secret", ip)
                self._note_block()
                return web.Response(status=404)
            return web.Response(status=404)

        cache = await self._get_secrets()
        matched_kind: Literal["bot", "router", "website"] | None = None
        matched_id: int | None = None
        for kind, eid, sec in cache:
            if hmac.compare_digest(secret, sec):
                if kind == "bot":
                    matched_kind = "bot"
                elif kind == "website":
                    matched_kind = "website"
                else:
                    matched_kind = "router"
                matched_id = eid
                break

        if matched_id is None:
            hits = self._register_unauth(ip)
            trusted = self._has_recent_success(ip)
            if hits > unauth_rpm:
                self._note_block()
                return web.Response(status=404)
            if trusted:
                return web.Response(status=429)
            banned = self._register_fail_maybe_ban(ip, fails_threshold, ban_duration_sec)
            if banned:
                logger.warning("heartbeat ban ip=%s reason=bad_secret", ip)
                self._note_block()
            return web.Response(status=404)

        # valid — учтём квоту авторизованных, чтобы NAT-массовый флуд тоже имел потолок
        now = time.time()
        dq = self._unauth_hits.setdefault(f"ok:{ip}", deque())
        cutoff = now - _UNAUTH_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()
        dq.append(now)
        if len(dq) > _VALID_HEARTBEAT_RPM:
            return web.Response(status=429)

        if matched_kind == "bot":
            try:
                await queries.touch_heartbeat(self._db, matched_id, ip)
            except Exception:
                logger.exception("touch_heartbeat failed")
            self._recent_success[ip] = now
            self._fails.pop(ip, None)
            return web.json_response({"ok": True, "bot_id": matched_id})

        body = await request.read()
        if len(body) > _MAX_HEARTBEAT_BODY:
            return web.json_response({"ok": False, "error": "body_too_large"}, status=413)
        try:
            payload = parse_heartbeat_payload(body)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

        if matched_kind == "website":
            wid = matched_id
            assert wid is not None
            try:
                await queries.touch_website_heartbeat(self._db, wid, ip)
            except Exception:
                logger.exception("touch_website_heartbeat failed")

            if payload is not None and payload.site is not None:
                site = payload.site
                err = site.error
                if not site.ok and not err:
                    err = "unreachable"
                try:
                    await queries.apply_website_site_push(
                        self._db,
                        wid,
                        host=site.host,
                        resolved_ip=site.ip,
                        ok=site.ok,
                        latency_ms=site.latency_ms,
                        error_text=err,
                    )
                except Exception:
                    logger.exception("apply_website_site_push failed")

            matched_modules = 0
            ignored = 0
            if payload is not None and payload.checks:
                for item in payload.checks:
                    mod = await queries.find_website_module(
                        self._db,
                        wid,
                        module_id=item.target_id,
                    )
                    if mod is None or not mod.get("enabled", True):
                        ignored += 1
                        continue
                    mid = int(mod["id"])
                    err = item.error
                    if not item.ok and not err:
                        err = "unreachable"
                    await queries.apply_website_module_push(
                        self._db,
                        mid,
                        item.ok,
                        item.latency_ms,
                        err,
                    )
                    matched_modules += 1

            self._recent_success[ip] = now
            self._fails.pop(ip, None)
            return web.json_response(
                {
                    "ok": True,
                    "website_id": wid,
                    "matched": matched_modules,
                    "ignored": ignored,
                }
            )

        # router
        try:
            await queries.touch_router_heartbeat(self._db, matched_id, ip)
        except Exception:
            logger.exception("touch_router_heartbeat failed")

        matched_targets = 0
        ignored = 0
        if payload is not None and payload.checks:
            for item in payload.checks:
                target = await queries.find_router_target(
                    self._db,
                    matched_id,
                    target_id=item.target_id,
                    address=item.address,
                )
                if target is None or not target.get("enabled", True):
                    ignored += 1
                    continue
                tid = int(target["id"])
                err = item.error
                if not item.ok and not err:
                    err = "unreachable"
                await queries.apply_router_target_push(
                    self._db,
                    tid,
                    item.ok,
                    item.latency_ms,
                    err,
                )
                matched_targets += 1

        events_notified = 0
        events_skipped = 0
        if payload is not None and payload.events:
            router = await queries.get_monitored_router(self._db, matched_id)
            rname = str(router["display_name"]) if router else f"id={matched_id}"
            for ev in payload.events:
                _eid, do_notify, msg = await queries.record_router_event(
                    self._db,
                    matched_id,
                    rname,
                    ev.event_type,
                    ip if ip != "-" else None,
                    custom_text=ev.custom_text,
                )
                if do_notify:
                    events_notified += 1
                    if self._notify is not None:
                        try:
                            await self._notify(msg)
                        except Exception:
                            logger.exception(
                                "router event notify failed router_id=%s type=%s",
                                matched_id,
                                ev.event_type,
                            )
                else:
                    events_skipped += 1

        self._recent_success[ip] = now
        self._fails.pop(ip, None)
        return web.json_response(
            {
                "ok": True,
                "router_id": matched_id,
                "matched": matched_targets,
                "ignored": ignored,
                "events_notified": events_notified,
                "events_skipped": events_skipped,
            }
        )

    async def _handle_404(self, request: web.Request) -> web.Response:
        ip = _client_ip(request)

        if self._is_banned(ip):
            self._note_block()
            return web.Response(status=404)

        settings = await self._get_settings()
        unauth_rpm = int(settings["heartbeat_unauth_rate_per_min"])
        fails_threshold = int(settings["heartbeat_ban_fails_threshold"])
        ban_duration_sec = int(settings["heartbeat_ban_duration_min"]) * 60

        hits = self._register_unauth(ip)
        if hits > unauth_rpm:
            self._note_block()
            trusted = self._has_recent_success(ip)
            if not trusted:
                banned = self._register_fail_maybe_ban(ip, fails_threshold, ban_duration_sec)
                if banned:
                    logger.warning("heartbeat ban ip=%s reason=path_flood", ip)
        return web.Response(status=404)

    # ---- GC ----

    async def _gc_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_GC_INTERVAL_SEC)
                now = time.time()
                for d, window in (
                    (self._unauth_hits, _UNAUTH_WINDOW_SEC),
                    (self._fails, 3600),
                ):
                    empty_keys: list[str] = []
                    for k, dq in d.items():
                        cutoff = now - window
                        while dq and dq[0] < cutoff:
                            dq.popleft()
                        if not dq:
                            empty_keys.append(k)
                    for k in empty_keys:
                        d.pop(k, None)
                expired_bans = [ip for ip, t in self._bans.items() if t <= now]
                for ip in expired_bans:
                    self._bans.pop(ip, None)
                expired_success = [
                    ip
                    for ip, t in self._recent_success.items()
                    if now - t > _RECENT_SUCCESS_TTL_SEC
                ]
                for ip in expired_success:
                    self._recent_success.pop(ip, None)
                cutoff = now - 86400
                while self._blocked_24h and self._blocked_24h[0] < cutoff:
                    self._blocked_24h.popleft()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("heartbeat gc loop error")

    # ---- сервер ----

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_HEARTBEAT_BODY)
        app.router.add_post("/heartbeat", self._handle_heartbeat)
        app.router.add_get("/heartbeat", self._handle_heartbeat)
        # catch-all: любой метод, любой путь — тихий 404
        app.router.add_route("*", "/{tail:.*}", self._handle_404)
        return app

    async def start(self) -> None:
        app = self._build_app()
        self._runner = web.AppRunner(app, access_log=None, server_header=False)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner, host="0.0.0.0", port=self._port, shutdown_timeout=5
        )
        await site.start()
        self._gc_task = asyncio.create_task(self._gc_loop(), name="botping-heartbeat-gc")
        logger.info("Heartbeat server listening on 0.0.0.0:%s", self._port)

    async def stop(self) -> None:
        if self._gc_task is not None:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass
            self._gc_task = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


def start_heartbeat_server(
    db: Database, port: int, *, notify: NotifyFn | None = None
) -> tuple[HeartbeatServer, asyncio.Task[Any]]:
    server = HeartbeatServer(db, port, notify=notify)

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
