from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import aiosqlite

from botping.router_events import ROUTER_EVENT_DEDUP_SEC, format_router_event_message
from botping.timeutil import MOSCOW_TZ, now_moscow_iso

if TYPE_CHECKING:
    from botping.db.pool import Database

DEFAULT_SETTINGS: dict[str, str] = {
    "check_interval_sec": "60",
    "request_timeout_sec": "15",
    "fail_threshold": "2",
    "repeat_alert_interval_sec": "3600",
    "slow_ms": "0",
    "quiet_hours": "{}",
    "daily_excel_report_enabled": "0",
    "disk_usage_threshold_pct": "80",
    "disk_check_interval_sec": "300",
    "telegram_api_probe_enabled": "1",
    "heartbeat_timeout_sec": "120",
    "heartbeat_port": "8080",
    "heartbeat_unauth_rate_per_min": "10",
    "heartbeat_ban_fails_threshold": "6",
    "heartbeat_ban_duration_min": "15",
}


async def ensure_defaults(conn: aiosqlite.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        await conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    await conn.commit()


async def get_setting(db: Database, key: str) -> str | None:
    row = await db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return str(row[0]) if row else None


async def set_setting(db: Database, key: str, value: str, admin_chat_id: int | None = None) -> None:
    row = await db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    old = str(row[0]) if row else None
    await db.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    if admin_chat_id is not None and old != value:
        await db.execute(
            "INSERT INTO settings_audit (ts, admin_chat_id, key, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
            (now_moscow_iso(), admin_chat_id, key, old, value),
        )


_BOT_COLUMNS = (
    "id, display_name, token, enabled, created_at, "
    "heartbeat_secret, last_heartbeat_at, last_heartbeat_ip"
)


def _row_to_bot(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "display_name": r[1],
        "token": r[2],
        "enabled": bool(r[3]),
        "created_at": r[4],
        "heartbeat_secret": r[5] or "",
        "last_heartbeat_at": r[6],
        "last_heartbeat_ip": r[7],
    }


async def list_monitored_bots(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        f"SELECT {_BOT_COLUMNS} FROM monitored_bots ORDER BY id"
    )
    return [_row_to_bot(r) for r in rows]


async def get_monitored_bot(db: Database, bot_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        f"SELECT {_BOT_COLUMNS} FROM monitored_bots WHERE id = ?",
        (bot_id,),
    )
    if not r:
        return None
    return _row_to_bot(r)


async def insert_monitored_bot(
    db: Database, display_name: str, token: str, heartbeat_secret: str
) -> int:
    row = await db.write_returning_one(
        """
        INSERT INTO monitored_bots
            (display_name, token, enabled, created_at, heartbeat_secret)
        VALUES (?, ?, 1, ?, ?) RETURNING id
        """,
        (display_name.strip(), token.strip(), now_moscow_iso(), heartbeat_secret),
    )
    assert row is not None
    return int(row[0])


async def find_bot_by_heartbeat_secret(
    db: Database, secret: str
) -> dict[str, Any] | None:
    if not secret:
        return None
    r = await db.fetchone(
        f"SELECT {_BOT_COLUMNS} FROM monitored_bots WHERE heartbeat_secret = ?",
        (secret,),
    )
    if not r:
        return None
    return _row_to_bot(r)


async def touch_heartbeat(db: Database, bot_id: int, ip: str | None) -> None:
    await db.execute(
        "UPDATE monitored_bots SET last_heartbeat_at = ?, last_heartbeat_ip = ? WHERE id = ?",
        (now_moscow_iso(), (ip or "")[:64] or None, bot_id),
    )


async def regenerate_heartbeat_secret(db: Database, bot_id: int, new_secret: str) -> None:
    await db.execute(
        "UPDATE monitored_bots SET heartbeat_secret = ? WHERE id = ?",
        (new_secret, bot_id),
    )


async def list_heartbeat_secrets(db: Database) -> list[tuple[int, str]]:
    """Пары (bot_id, secret) для in-memory кэша в heartbeat-сервере.
    Токены не тянем — они не нужны для проверки пинга."""
    rows = await db.fetchall(
        "SELECT id, heartbeat_secret FROM monitored_bots WHERE heartbeat_secret IS NOT NULL AND heartbeat_secret != ''"
    )
    return [(int(r[0]), str(r[1])) for r in rows]


async def list_heartbeat_secrets_for_cache(
    db: Database,
) -> list[tuple[str, int, str]]:
    """(kind, entity_id, secret) — kind: 'bot' | 'router'."""
    bots = await list_heartbeat_secrets(db)
    out: list[tuple[str, int, str]] = [("bot", bid, sec) for bid, sec in bots]
    rows = await db.fetchall(
        "SELECT id, heartbeat_secret FROM monitored_routers WHERE heartbeat_secret != ''"
    )
    for r in rows:
        out.append(("router", int(r[0]), str(r[1])))
    return out


async def heartbeat_secret_in_use(db: Database, secret: str, exclude: str | None = None) -> bool:
    ex = exclude or ""
    r = await db.fetchone(
        "SELECT 1 FROM monitored_bots WHERE heartbeat_secret = ? AND heartbeat_secret != ? LIMIT 1",
        (secret, ex),
    )
    if r:
        return True
    r2 = await db.fetchone(
        "SELECT 1 FROM monitored_routers WHERE heartbeat_secret = ? AND heartbeat_secret != ? LIMIT 1",
        (secret, ex),
    )
    return r2 is not None


async def update_bot_enabled(db: Database, bot_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE monitored_bots SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, bot_id),
    )


async def delete_monitored_bot(db: Database, bot_id: int) -> None:
    await db.execute("DELETE FROM monitored_bots WHERE id = ?", (bot_id,))


async def insert_check(
    db: Database,
    bot_id: int,
    ok: bool,
    latency_ms: int | None,
    http_status: int | None,
    error_text: str | None,
    rate_limited: bool,
    check_type: str = "getupdates",
) -> None:
    await db.execute(
        """
        INSERT INTO checks (bot_id, ok, latency_ms, http_status, error_text, rate_limited, ts, check_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bot_id,
            1 if ok else 0,
            latency_ms,
            http_status,
            (error_text or "")[:500],
            1 if rate_limited else 0,
            now_moscow_iso(),
            check_type,
        ),
    )


async def get_last_check(db: Database, bot_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, bot_id, ts, ok, latency_ms, http_status, error_text, rate_limited, check_type
        FROM checks WHERE bot_id = ? ORDER BY id DESC LIMIT 1
        """,
        (bot_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "bot_id": r[1],
        "ts": r[2],
        "ok": bool(r[3]),
        "latency_ms": r[4],
        "http_status": r[5],
        "error_text": r[6] or "",
        "rate_limited": bool(r[7]),
        "check_type": r[8] or "getupdates",
    }


async def open_incident(db: Database, bot_id: int, last_error: str | None) -> int:
    ts = now_moscow_iso()
    row = await db.write_returning_one(
        """
        INSERT INTO incidents (bot_id, last_error, started_at, last_alert_at)
        VALUES (?, ?, ?, ?) RETURNING id
        """,
        (bot_id, (last_error or "")[:500], ts, ts),
    )
    assert row is not None
    return int(row[0])


async def get_open_incident(db: Database, bot_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, bot_id, started_at, ended_at, last_error, last_alert_at
        FROM incidents WHERE bot_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1
        """,
        (bot_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "bot_id": r[1],
        "started_at": r[2],
        "ended_at": r[3],
        "last_error": r[4],
        "last_alert_at": r[5],
    }


async def update_incident_error(db: Database, incident_id: int, last_error: str) -> None:
    await db.execute(
        "UPDATE incidents SET last_error = ? WHERE id = ?",
        ((last_error or "")[:500], incident_id),
    )


async def touch_incident_alert(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE incidents SET last_alert_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def close_incident(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE incidents SET ended_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


# ── Telegram API probe (вторичная проверка доступности Telegram в целом) ──

async def insert_telegram_check(
    db: Database,
    ok: bool,
    latency_ms: int | None,
    http_status: int | None,
    error_text: str | None,
    rate_limited: bool,
) -> None:
    await db.execute(
        """
        INSERT INTO telegram_checks (ts, ok, latency_ms, http_status, error_text, rate_limited)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now_moscow_iso(),
            1 if ok else 0,
            latency_ms,
            http_status,
            (error_text or "")[:500],
            1 if rate_limited else 0,
        ),
    )


async def get_last_telegram_check(db: Database) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, ts, ok, latency_ms, http_status, error_text, rate_limited
        FROM telegram_checks ORDER BY id DESC LIMIT 1
        """
    )
    if not r:
        return None
    return {
        "id": r[0],
        "ts": r[1],
        "ok": bool(r[2]),
        "latency_ms": r[3],
        "http_status": r[4],
        "error_text": r[5] or "",
        "rate_limited": bool(r[6]),
    }


async def open_telegram_incident(db: Database, last_error: str | None) -> int:
    ts = now_moscow_iso()
    row = await db.write_returning_one(
        """
        INSERT INTO telegram_incidents (started_at, last_error, last_alert_at)
        VALUES (?, ?, ?) RETURNING id
        """,
        (ts, (last_error or "")[:500], ts),
    )
    assert row is not None
    return int(row[0])


async def get_open_telegram_incident(db: Database) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, started_at, ended_at, last_error, last_alert_at
        FROM telegram_incidents WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1
        """
    )
    if not r:
        return None
    return {
        "id": r[0],
        "started_at": r[1],
        "ended_at": r[2],
        "last_error": r[3],
        "last_alert_at": r[4],
    }


async def update_telegram_incident_error(
    db: Database, incident_id: int, last_error: str
) -> None:
    await db.execute(
        "UPDATE telegram_incidents SET last_error = ? WHERE id = ?",
        ((last_error or "")[:500], incident_id),
    )


async def touch_telegram_incident_alert(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE telegram_incidents SET last_alert_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def close_telegram_incident(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE telegram_incidents SET ended_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


EXPORT_CHECKS_LIMIT = 200_000
EXPORT_INCIDENTS_LIMIT = 50_000
EXPORT_AUDIT_LIMIT = 50_000


async def export_checks_for_report(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_CHECKS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT c.id, c.bot_id, b.display_name, c.ts, c.ok, c.latency_ms, c.http_status, c.error_text, c.rate_limited, c.check_type
        FROM checks c
        JOIN monitored_bots b ON b.id = c.bot_id
        WHERE c.ts >= ? AND c.ts <= ?
        ORDER BY c.ts ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_CHECKS_LIMIT
    if truncated:
        rows = rows[:EXPORT_CHECKS_LIMIT]
    out = [
        {
            "id": r[0],
            "bot_id": r[1],
            "display_name": r[2],
            "ts": r[3],
            "ok": bool(r[4]),
            "latency_ms": r[5],
            "http_status": r[6],
            "error_text": r[7] or "",
            "rate_limited": bool(r[8]),
            "check_type": r[9] or "getupdates",
        }
        for r in rows
    ]
    return out, truncated


EXPORT_TELEGRAM_CHECKS_LIMIT = 200_000
EXPORT_TELEGRAM_INCIDENTS_LIMIT = 10_000


async def export_telegram_checks_for_report(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_TELEGRAM_CHECKS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT id, ts, ok, latency_ms, http_status, error_text, rate_limited
        FROM telegram_checks
        WHERE ts >= ? AND ts <= ?
        ORDER BY ts ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_TELEGRAM_CHECKS_LIMIT
    if truncated:
        rows = rows[:EXPORT_TELEGRAM_CHECKS_LIMIT]
    out = [
        {
            "id": r[0],
            "ts": r[1],
            "ok": bool(r[2]),
            "latency_ms": r[3],
            "http_status": r[4],
            "error_text": r[5] or "",
            "rate_limited": bool(r[6]),
        }
        for r in rows
    ]
    return out, truncated


async def export_telegram_incidents_overlapping(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_TELEGRAM_INCIDENTS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT id, started_at, ended_at, last_error, last_alert_at
        FROM telegram_incidents
        WHERE (ended_at IS NULL OR ended_at >= ?) AND started_at <= ?
        ORDER BY started_at ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_TELEGRAM_INCIDENTS_LIMIT
    if truncated:
        rows = rows[:EXPORT_TELEGRAM_INCIDENTS_LIMIT]
    out = [
        {
            "id": r[0],
            "started_at": r[1],
            "ended_at": r[2],
            "last_error": r[3] or "",
            "last_alert_at": r[4],
        }
        for r in rows
    ]
    return out, truncated


async def export_incidents_overlapping(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_INCIDENTS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT i.id, i.bot_id, b.display_name, i.started_at, i.ended_at, i.last_error, i.last_alert_at
        FROM incidents i
        JOIN monitored_bots b ON b.id = i.bot_id
        WHERE (i.ended_at IS NULL OR i.ended_at >= ?) AND i.started_at <= ?
        ORDER BY i.started_at ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_INCIDENTS_LIMIT
    if truncated:
        rows = rows[:EXPORT_INCIDENTS_LIMIT]
    out = [
        {
            "id": r[0],
            "bot_id": r[1],
            "display_name": r[2],
            "started_at": r[3],
            "ended_at": r[4],
            "last_error": r[5] or "",
            "last_alert_at": r[6],
        }
        for r in rows
    ]
    return out, truncated


async def export_settings_audit_for_report(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_AUDIT_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT ts, admin_chat_id, key, old_value, new_value
        FROM settings_audit
        WHERE ts >= ? AND ts <= ?
        ORDER BY ts ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_AUDIT_LIMIT
    if truncated:
        rows = rows[:EXPORT_AUDIT_LIMIT]
    out = [
        {
            "ts": r[0],
            "admin_chat_id": r[1],
            "key": r[2],
            "old_value": r[3],
            "new_value": r[4],
        }
        for r in rows
    ]
    return out, truncated


async def list_settings_raw_pairs(db: Database) -> list[tuple[str, str]]:
    rows = await db.fetchall("SELECT key, value FROM settings ORDER BY key")
    return [(str(r[0]), str(r[1])) for r in rows]


def _is_hidden_from_report_settings_key(key: str) -> bool:
    return key.startswith("daily_excel_report_last_")


async def list_settings_raw_pairs_for_report(db: Database) -> list[tuple[str, str]]:
    """Пары настроек для листа Excel без служебных ключей ежедневного отчёта."""
    pairs = await list_settings_raw_pairs(db)
    return [(k, v) for k, v in pairs if not _is_hidden_from_report_settings_key(k)]


async def get_checks_storage_stats(db: Database) -> dict[str, Any]:
    """Сколько проверок в БД и диапазон меток времени (как в БД: пояс Москва)."""
    r = await db.fetchone("SELECT COUNT(*), MIN(ts), MAX(ts) FROM checks")
    if not r:
        return {"count": 0, "min_ts": None, "max_ts": None}
    c = int(r[0])
    return {
        "count": c,
        "min_ts": str(r[1]) if r[1] is not None else None,
        "max_ts": str(r[2]) if r[2] is not None else None,
    }


async def list_incidents_in_range(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        """
        SELECT i.id, i.bot_id, b.display_name, i.started_at, i.ended_at, i.last_error
        FROM incidents i
        JOIN monitored_bots b ON b.id = i.bot_id
        WHERE i.started_at >= ? AND i.started_at <= ?
        ORDER BY i.started_at DESC
        LIMIT 500
        """,
        (start_iso, end_iso),
    )
    return [
        {
            "id": r[0],
            "bot_id": r[1],
            "display_name": r[2],
            "started_at": r[3],
            "ended_at": r[4],
            "last_error": r[5] or "",
        }
        for r in rows
    ]


def parse_settings_row(settings: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["check_interval_sec"] = max(5, int(settings.get("check_interval_sec", "60")))
    out["request_timeout_sec"] = max(1, int(settings.get("request_timeout_sec", "15")))
    out["fail_threshold"] = max(1, int(settings.get("fail_threshold", "2")))
    out["repeat_alert_interval_sec"] = max(60, int(settings.get("repeat_alert_interval_sec", "3600")))
    out["slow_ms"] = max(0, int(settings.get("slow_ms", "0")))
    qh = settings.get("quiet_hours", "{}")
    try:
        out["quiet_hours"] = json.loads(qh) if qh else {}
    except json.JSONDecodeError:
        out["quiet_hours"] = {}
    v = (settings.get("daily_excel_report_enabled") or "0").strip()
    out["daily_excel_report_enabled"] = v == "1"
    out["disk_usage_threshold_pct"] = max(50, min(95, int(settings.get("disk_usage_threshold_pct", "80"))))
    out["disk_check_interval_sec"] = max(60, int(settings.get("disk_check_interval_sec", "300")))
    tp = (settings.get("telegram_api_probe_enabled") or "1").strip()
    out["telegram_api_probe_enabled"] = tp == "1"
    out["heartbeat_timeout_sec"] = max(30, int(settings.get("heartbeat_timeout_sec", "120")))
    out["heartbeat_port"] = max(1, min(65535, int(settings.get("heartbeat_port", "8080"))))
    out["heartbeat_unauth_rate_per_min"] = max(
        1, int(settings.get("heartbeat_unauth_rate_per_min", "10"))
    )
    out["heartbeat_ban_fails_threshold"] = max(
        1, int(settings.get("heartbeat_ban_fails_threshold", "6"))
    )
    out["heartbeat_ban_duration_min"] = max(
        1, min(1440, int(settings.get("heartbeat_ban_duration_min", "15")))
    )
    return out


async def load_all_settings(db: Database) -> dict[str, Any]:
    rows = await db.fetchall("SELECT key, value FROM settings")
    merged = dict(DEFAULT_SETTINGS)
    merged.update({str(r[0]): str(r[1]) for r in rows})
    return parse_settings_row(merged)


# ── Monitored routers / LAN targets ───────────────────────────────────

_ROUTER_COLUMNS = (
    "id, display_name, enabled, created_at, "
    "heartbeat_secret, last_heartbeat_at, last_heartbeat_ip"
)

_TARGET_COLUMNS = (
    "id, router_id, display_name, address, enabled, "
    "last_ok_at, last_latency_ms, last_error"
)


def _row_to_router(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "display_name": r[1],
        "enabled": bool(r[2]),
        "created_at": r[3],
        "heartbeat_secret": r[4] or "",
        "last_heartbeat_at": r[5],
        "last_heartbeat_ip": r[6],
    }


def _row_to_target(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "router_id": r[1],
        "display_name": r[2],
        "address": r[3],
        "enabled": bool(r[4]),
        "last_ok_at": r[5],
        "last_latency_ms": r[6],
        "last_error": r[7],
    }


async def list_monitored_routers(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        f"SELECT {_ROUTER_COLUMNS} FROM monitored_routers ORDER BY id"
    )
    return [_row_to_router(r) for r in rows]


async def get_monitored_router(db: Database, router_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        f"SELECT {_ROUTER_COLUMNS} FROM monitored_routers WHERE id = ?",
        (router_id,),
    )
    if not r:
        return None
    return _row_to_router(r)


async def insert_monitored_router(
    db: Database, display_name: str, heartbeat_secret: str
) -> int:
    row = await db.write_returning_one(
        """
        INSERT INTO monitored_routers
            (display_name, enabled, created_at, heartbeat_secret)
        VALUES (?, 1, ?, ?) RETURNING id
        """,
        (display_name.strip(), now_moscow_iso(), heartbeat_secret),
    )
    assert row is not None
    return int(row[0])


async def touch_router_heartbeat(db: Database, router_id: int, ip: str | None) -> None:
    await db.execute(
        "UPDATE monitored_routers SET last_heartbeat_at = ?, last_heartbeat_ip = ? WHERE id = ?",
        (now_moscow_iso(), (ip or "")[:64] or None, router_id),
    )


async def regenerate_router_heartbeat_secret(
    db: Database, router_id: int, new_secret: str
) -> None:
    await db.execute(
        "UPDATE monitored_routers SET heartbeat_secret = ? WHERE id = ?",
        (new_secret, router_id),
    )


async def update_router_enabled(db: Database, router_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE monitored_routers SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, router_id),
    )


async def delete_monitored_router(db: Database, router_id: int) -> None:
    await db.execute("DELETE FROM monitored_routers WHERE id = ?", (router_id,))


async def list_router_targets(
    db: Database, router_id: int, *, enabled_only: bool = False
) -> list[dict[str, Any]]:
    sql = f"SELECT {_TARGET_COLUMNS} FROM router_targets WHERE router_id = ?"
    params: tuple[Any, ...] = (router_id,)
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY id"
    rows = await db.fetchall(sql, params)
    return [_row_to_target(r) for r in rows]


async def get_router_target(db: Database, target_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        f"SELECT {_TARGET_COLUMNS} FROM router_targets WHERE id = ?",
        (target_id,),
    )
    if not r:
        return None
    return _row_to_target(r)


async def insert_router_target(
    db: Database, router_id: int, display_name: str, address: str
) -> int:
    row = await db.write_returning_one(
        """
        INSERT INTO router_targets (router_id, display_name, address, enabled)
        VALUES (?, ?, ?, 1) RETURNING id
        """,
        (router_id, display_name.strip(), address.strip()),
    )
    assert row is not None
    return int(row[0])


async def update_router_target_enabled(
    db: Database, target_id: int, enabled: bool
) -> None:
    await db.execute(
        "UPDATE router_targets SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, target_id),
    )


async def delete_router_target(db: Database, target_id: int) -> None:
    await db.execute("DELETE FROM router_targets WHERE id = ?", (target_id,))


async def find_router_target(
    db: Database,
    router_id: int,
    *,
    target_id: int | None = None,
    address: str | None = None,
) -> dict[str, Any] | None:
    if target_id is not None:
        r = await db.fetchone(
            f"SELECT {_TARGET_COLUMNS} FROM router_targets WHERE router_id = ? AND id = ?",
            (router_id, target_id),
        )
        if r:
            return _row_to_target(r)
    if address:
        addr = address.strip()
        rows = await db.fetchall(
            f"SELECT {_TARGET_COLUMNS} FROM router_targets WHERE router_id = ?",
            (router_id,),
        )
        addr_lower = addr.lower()
        for row in rows:
            t = _row_to_target(row)
            if str(t["address"]).strip().lower() == addr_lower:
                return t
    return None


async def apply_router_target_push(
    db: Database,
    target_id: int,
    ok: bool,
    latency_ms: int | None,
    error_text: str | None,
) -> None:
    ts = now_moscow_iso()
    err_val = None if ok else ((error_text or "")[:500] or "unreachable")
    await db.execute(
        """
        UPDATE router_targets
        SET last_ok_at = ?, last_latency_ms = ?, last_error = ?
        WHERE id = ?
        """,
        (ts, latency_ms, err_val, target_id),
    )
    await insert_router_target_check(
        db, target_id, ok, latency_ms, error_text, check_type="lan_push"
    )


async def insert_router_target_check(
    db: Database,
    target_id: int,
    ok: bool,
    latency_ms: int | None,
    error_text: str | None,
    check_type: str = "lan_push",
) -> None:
    await db.execute(
        """
        INSERT INTO router_target_checks (target_id, ok, latency_ms, error_text, ts, check_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            target_id,
            1 if ok else 0,
            latency_ms,
            (error_text or "")[:500],
            now_moscow_iso(),
            check_type,
        ),
    )


async def open_router_incident(db: Database, router_id: int, last_error: str | None) -> int:
    ts = now_moscow_iso()
    row = await db.write_returning_one(
        """
        INSERT INTO router_incidents (router_id, last_error, started_at, last_alert_at)
        VALUES (?, ?, ?, ?) RETURNING id
        """,
        (router_id, (last_error or "")[:500], ts, ts),
    )
    assert row is not None
    return int(row[0])


async def get_open_router_incident(db: Database, router_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, router_id, started_at, ended_at, last_error, last_alert_at
        FROM router_incidents WHERE router_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1
        """,
        (router_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "router_id": r[1],
        "started_at": r[2],
        "ended_at": r[3],
        "last_error": r[4],
        "last_alert_at": r[5],
    }


async def update_router_incident_error(db: Database, incident_id: int, last_error: str) -> None:
    await db.execute(
        "UPDATE router_incidents SET last_error = ? WHERE id = ?",
        ((last_error or "")[:500], incident_id),
    )


async def touch_router_incident_alert(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE router_incidents SET last_alert_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def close_router_incident(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE router_incidents SET ended_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def open_router_target_incident(
    db: Database, target_id: int, last_error: str | None
) -> int:
    ts = now_moscow_iso()
    row = await db.write_returning_one(
        """
        INSERT INTO router_target_incidents (target_id, last_error, started_at, last_alert_at)
        VALUES (?, ?, ?, ?) RETURNING id
        """,
        (target_id, (last_error or "")[:500], ts, ts),
    )
    assert row is not None
    return int(row[0])


async def get_open_router_target_incident(
    db: Database, target_id: int
) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, target_id, started_at, ended_at, last_error, last_alert_at
        FROM router_target_incidents WHERE target_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1
        """,
        (target_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "target_id": r[1],
        "started_at": r[2],
        "ended_at": r[3],
        "last_error": r[4],
        "last_alert_at": r[5],
    }


async def update_router_target_incident_error(
    db: Database, incident_id: int, last_error: str
) -> None:
    await db.execute(
        "UPDATE router_target_incidents SET last_error = ? WHERE id = ?",
        ((last_error or "")[:500], incident_id),
    )


async def touch_router_target_incident_alert(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE router_target_incidents SET last_alert_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def close_router_target_incident(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE router_target_incidents SET ended_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def list_all_incidents_in_range(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> list[dict[str, Any]]:
    """Инциденты ботов, роутеров и LAN-целей за период."""
    out: list[dict[str, Any]] = []
    bot_rows = await db.fetchall(
        """
        SELECT i.id, i.bot_id, b.display_name, i.started_at, i.ended_at, i.last_error
        FROM incidents i
        JOIN monitored_bots b ON b.id = i.bot_id
        WHERE i.started_at >= ? AND i.started_at <= ?
        ORDER BY i.started_at DESC LIMIT 200
        """,
        (start_iso, end_iso),
    )
    for r in bot_rows:
        out.append(
            {
                "entity_type": "bot",
                "id": r[0],
                "entity_id": r[1],
                "display_name": r[2],
                "started_at": r[3],
                "ended_at": r[4],
                "last_error": r[5] or "",
            }
        )
    router_rows = await db.fetchall(
        """
        SELECT i.id, i.router_id, r.display_name, i.started_at, i.ended_at, i.last_error
        FROM router_incidents i
        JOIN monitored_routers r ON r.id = i.router_id
        WHERE i.started_at >= ? AND i.started_at <= ?
        ORDER BY i.started_at DESC LIMIT 200
        """,
        (start_iso, end_iso),
    )
    for r in router_rows:
        out.append(
            {
                "entity_type": "router",
                "id": r[0],
                "entity_id": r[1],
                "display_name": r[2],
                "started_at": r[3],
                "ended_at": r[4],
                "last_error": r[5] or "",
            }
        )
    target_rows = await db.fetchall(
        """
        SELECT i.id, i.target_id, t.display_name, r.display_name, t.address,
               i.started_at, i.ended_at, i.last_error
        FROM router_target_incidents i
        JOIN router_targets t ON t.id = i.target_id
        JOIN monitored_routers r ON r.id = t.router_id
        WHERE i.started_at >= ? AND i.started_at <= ?
        ORDER BY i.started_at DESC LIMIT 200
        """,
        (start_iso, end_iso),
    )
    for r in target_rows:
        out.append(
            {
                "entity_type": "target",
                "id": r[0],
                "entity_id": r[1],
                "display_name": f"{r[3]} / {r[2]} ({r[4]})",
                "started_at": r[5],
                "ended_at": r[6],
                "last_error": r[7] or "",
            }
        )
    out.sort(key=lambda x: str(x["started_at"]), reverse=True)
    return out[:500]


EXPORT_ROUTER_TARGET_CHECKS_LIMIT = 200_000


async def export_router_target_checks_for_report(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_ROUTER_TARGET_CHECKS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT c.id, c.target_id, t.display_name, r.display_name, t.address,
               c.ts, c.ok, c.latency_ms, c.error_text, c.check_type
        FROM router_target_checks c
        JOIN router_targets t ON t.id = c.target_id
        JOIN monitored_routers r ON r.id = t.router_id
        WHERE c.ts >= ? AND c.ts <= ?
        ORDER BY c.ts ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_ROUTER_TARGET_CHECKS_LIMIT
    if truncated:
        rows = rows[:EXPORT_ROUTER_TARGET_CHECKS_LIMIT]
    out = [
        {
            "id": r[0],
            "target_id": r[1],
            "target_name": r[2],
            "router_name": r[3],
            "address": r[4],
            "ts": r[5],
            "ok": bool(r[6]),
            "latency_ms": r[7],
            "error_text": r[8] or "",
            "check_type": r[9] or "lan_push",
        }
        for r in rows
    ]
    return out, truncated


async def export_router_incidents_overlapping(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_INCIDENTS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT i.id, i.router_id, r.display_name, i.started_at, i.ended_at, i.last_error, i.last_alert_at
        FROM router_incidents i
        JOIN monitored_routers r ON r.id = i.router_id
        WHERE (i.ended_at IS NULL OR i.ended_at >= ?) AND i.started_at <= ?
        ORDER BY i.started_at ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_INCIDENTS_LIMIT
    if truncated:
        rows = rows[:EXPORT_INCIDENTS_LIMIT]
    out = [
        {
            "id": r[0],
            "router_id": r[1],
            "display_name": r[2],
            "started_at": r[3],
            "ended_at": r[4],
            "last_error": r[5] or "",
            "last_alert_at": r[6],
        }
        for r in rows
    ]
    return out, truncated


async def export_router_target_incidents_overlapping(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_INCIDENTS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT i.id, i.target_id, t.display_name, r.display_name, t.address,
               i.started_at, i.ended_at, i.last_error, i.last_alert_at
        FROM router_target_incidents i
        JOIN router_targets t ON t.id = i.target_id
        JOIN monitored_routers r ON r.id = t.router_id
        WHERE (i.ended_at IS NULL OR i.ended_at >= ?) AND i.started_at <= ?
        ORDER BY i.started_at ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_INCIDENTS_LIMIT
    if truncated:
        rows = rows[:EXPORT_INCIDENTS_LIMIT]
    out = [
        {
            "id": r[0],
            "target_id": r[1],
            "target_name": r[2],
            "router_name": r[3],
            "address": r[4],
            "started_at": r[5],
            "ended_at": r[6],
            "last_error": r[7] or "",
            "last_alert_at": r[8],
        }
        for r in rows
    ]
    return out, truncated


# ── Router events (WAN/LTE, custom) ─────────────────────────────────


def _router_event_dedup_cutoff_iso() -> str:
    from datetime import datetime, timedelta

    cutoff = datetime.now(MOSCOW_TZ) - timedelta(seconds=ROUTER_EVENT_DEDUP_SEC)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


async def router_event_recent_duplicate(
    db: Database, router_id: int, event_type: str
) -> bool:
    cutoff = _router_event_dedup_cutoff_iso()
    row = await db.fetchone(
        """
        SELECT id FROM router_events
        WHERE router_id = ? AND event_type = ? AND created_at >= ?
        LIMIT 1
        """,
        (router_id, event_type, cutoff),
    )
    return row is not None


async def insert_router_event(
    db: Database,
    router_id: int,
    event_type: str,
    message: str,
    source_ip: str | None,
) -> int:
    row = await db.write_returning_one(
        """
        INSERT INTO router_events (router_id, event_type, message, created_at, source_ip)
        VALUES (?, ?, ?, ?, ?) RETURNING id
        """,
        (router_id, event_type, message[:1000], now_moscow_iso(), source_ip),
    )
    assert row is not None
    return int(row[0])


async def record_router_event(
    db: Database,
    router_id: int,
    router_name: str,
    event_type: str,
    source_ip: str | None,
    *,
    custom_text: str | None = None,
) -> tuple[int, bool, str]:
    """
    Сохраняет событие. Возвращает (id, notify_telegram, message).
    notify_telegram=False при дедупе за ROUTER_EVENT_DEDUP_SEC.
    """
    message = format_router_event_message(
        router_name, event_type, custom_text=custom_text
    )
    if await router_event_recent_duplicate(db, router_id, event_type):
        return None, False, message
    eid = await insert_router_event(db, router_id, event_type, message, source_ip)
    return eid, True, message


async def get_latest_router_event(db: Database, router_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, router_id, event_type, message, created_at, source_ip
        FROM router_events WHERE router_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (router_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "router_id": r[1],
        "event_type": r[2],
        "message": r[3],
        "created_at": r[4],
        "source_ip": r[5],
    }


async def list_router_events(
    db: Database, router_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    lim = max(1, min(50, limit))
    rows = await db.fetchall(
        """
        SELECT id, event_type, message, created_at
        FROM router_events WHERE router_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (router_id, lim),
    )
    return [
        {
            "id": r[0],
            "event_type": r[1],
            "message": r[2],
            "created_at": r[3],
        }
        for r in rows
    ]


# ── Disk guard: cleanup queries ──────────────────────────────────────

async def count_checks(db: Database) -> int:
    row = await db.fetchone("SELECT COUNT(*) FROM checks")
    return int(row[0]) if row else 0


async def delete_oldest_checks(db: Database, limit: int) -> int:
    cur = await db.execute(
        "DELETE FROM checks WHERE id IN "
        "(SELECT id FROM checks ORDER BY id ASC LIMIT ?)",
        (limit,),
    )
    return cur.rowcount


async def delete_oldest_incidents_closed(db: Database, limit: int) -> int:
    cur = await db.execute(
        "DELETE FROM incidents WHERE ended_at IS NOT NULL AND id IN "
        "(SELECT id FROM incidents WHERE ended_at IS NOT NULL ORDER BY id ASC LIMIT ?)",
        (limit,),
    )
    return cur.rowcount


async def delete_oldest_audit(db: Database, limit: int) -> int:
    cur = await db.execute(
        "DELETE FROM settings_audit WHERE id IN "
        "(SELECT id FROM settings_audit ORDER BY id ASC LIMIT ?)",
        (limit,),
    )
    return cur.rowcount
