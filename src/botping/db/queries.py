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
    "telegram_api_check_interval_sec": "300",
    "telegram_api_fail_threshold": "3",
    "telegram_api_recover_threshold": "2",
    "telegram_api_down_alert_sec": "600",
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
    """(kind, entity_id, secret) — kind: 'bot' | 'router' | 'website'."""
    bots = await list_heartbeat_secrets(db)
    out: list[tuple[str, int, str]] = [("bot", bid, sec) for bid, sec in bots]
    rows = await db.fetchall(
        "SELECT id, heartbeat_secret FROM monitored_routers WHERE heartbeat_secret != ''"
    )
    for r in rows:
        out.append(("router", int(r[0]), str(r[1])))
    wrows = await db.fetchall(
        "SELECT id, heartbeat_secret FROM monitored_websites WHERE heartbeat_secret != ''"
    )
    for r in wrows:
        out.append(("website", int(r[0]), str(r[1])))
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
    if r2:
        return True
    r3 = await db.fetchone(
        "SELECT 1 FROM monitored_websites WHERE heartbeat_secret = ? AND heartbeat_secret != ? LIMIT 1",
        (secret, ex),
    )
    return r3 is not None


async def update_bot_enabled(db: Database, bot_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE monitored_bots SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, bot_id),
    )


async def update_bot_display_name(db: Database, bot_id: int, display_name: str) -> None:
    await db.execute(
        "UPDATE monitored_bots SET display_name = ? WHERE id = ?",
        (display_name.strip(), bot_id),
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


async def get_telegram_check_stats_24h(db: Database) -> dict[str, Any]:
    r = await db.fetchone(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed,
            SUM(rate_limited) AS rate_limited,
            SUM(CASE WHEN error_text LIKE 'Connect%' THEN 1 ELSE 0 END) AS connect_err,
            SUM(CASE WHEN error_text LIKE 'Read%' THEN 1 ELSE 0 END) AS read_err,
            SUM(CASE WHEN error_text = 'timeout' THEN 1 ELSE 0 END) AS timeouts,
            AVG(CASE WHEN ok = 1 THEN latency_ms END) AS avg_ok_ms,
            AVG(CASE WHEN ok = 0 THEN latency_ms END) AS avg_fail_ms
        FROM telegram_checks
        WHERE ts >= datetime('now', '-24 hours')
        """
    )
    if not r or r[0] == 0:
        return {
            "total": 0,
            "failed": 0,
            "rate_limited": 0,
            "connect_err": 0,
            "read_err": 0,
            "timeouts": 0,
            "avg_ok_ms": None,
            "avg_fail_ms": None,
        }
    return {
        "total": int(r[0]),
        "failed": int(r[1] or 0),
        "rate_limited": int(r[2] or 0),
        "connect_err": int(r[3] or 0),
        "read_err": int(r[4] or 0),
        "timeouts": int(r[5] or 0),
        "avg_ok_ms": int(r[6]) if r[6] is not None else None,
        "avg_fail_ms": int(r[7]) if r[7] is not None else None,
    }


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


async def open_telegram_incident(
    db: Database, last_error: str | None, *, user_alerted: bool = True
) -> int:
    ts = now_moscow_iso()
    alert_ts = ts if user_alerted else None
    row = await db.write_returning_one(
        """
        INSERT INTO telegram_incidents (started_at, last_error, last_alert_at)
        VALUES (?, ?, ?) RETURNING id
        """,
        (ts, (last_error or "")[:500], alert_ts),
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
    out["telegram_api_check_interval_sec"] = max(
        60, int(settings.get("telegram_api_check_interval_sec", "300"))
    )
    out["telegram_api_fail_threshold"] = max(
        1, int(settings.get("telegram_api_fail_threshold", "3"))
    )
    out["telegram_api_recover_threshold"] = max(
        1, int(settings.get("telegram_api_recover_threshold", "2"))
    )
    out["telegram_api_down_alert_sec"] = max(
        0, int(settings.get("telegram_api_down_alert_sec", "600"))
    )
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


async def update_router_display_name(db: Database, router_id: int, display_name: str) -> None:
    await db.execute(
        "UPDATE monitored_routers SET display_name = ? WHERE id = ?",
        (display_name.strip(), router_id),
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


async def update_router_target_display_name(
    db: Database, target_id: int, display_name: str
) -> None:
    await db.execute(
        "UPDATE router_targets SET display_name = ? WHERE id = ?",
        (display_name.strip(), target_id),
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
    website_rows = await db.fetchall(
        """
        SELECT i.id, i.website_id, w.display_name, w.host, i.started_at, i.ended_at, i.last_error
        FROM website_incidents i
        JOIN monitored_websites w ON w.id = i.website_id
        WHERE i.started_at >= ? AND i.started_at <= ?
        ORDER BY i.started_at DESC LIMIT 200
        """,
        (start_iso, end_iso),
    )
    for r in website_rows:
        out.append(
            {
                "entity_type": "website",
                "id": r[0],
                "entity_id": r[1],
                "display_name": f"{r[2]} ({r[3]})",
                "started_at": r[4],
                "ended_at": r[5],
                "last_error": r[6] or "",
            }
        )
    mod_rows = await db.fetchall(
        """
        SELECT i.id, i.module_id, m.display_name, w.display_name, w.host,
               i.started_at, i.ended_at, i.last_error
        FROM website_module_incidents i
        JOIN website_modules m ON m.id = i.module_id
        JOIN monitored_websites w ON w.id = m.website_id
        WHERE i.started_at >= ? AND i.started_at <= ?
        ORDER BY i.started_at DESC LIMIT 200
        """,
        (start_iso, end_iso),
    )
    for r in mod_rows:
        out.append(
            {
                "entity_type": "web_module",
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


# ── Monitored websites / modules ──────────────────────────────────────

_WEBSITE_COLUMNS = (
    "id, display_name, host, enabled, created_at, heartbeat_secret, "
    "last_heartbeat_at, last_heartbeat_ip, last_resolved_ip, "
    "last_site_ok_at, last_site_latency_ms, last_site_error"
)

_MODULE_COLUMNS = (
    "id, website_id, display_name, check_hint, enabled, "
    "last_ok_at, last_latency_ms, last_error"
)


def _row_to_website(r: aiosqlite.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": r[0],
        "display_name": r[1],
        "host": r[2],
        "enabled": bool(r[3]),
        "created_at": r[4],
        "heartbeat_secret": r[5],
        "last_heartbeat_at": r[6],
        "last_heartbeat_ip": r[7],
        "last_resolved_ip": r[8],
        "last_site_ok_at": r[9],
        "last_site_latency_ms": r[10],
        "last_site_error": r[11],
    }


def _row_to_module(r: aiosqlite.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": r[0],
        "website_id": r[1],
        "display_name": r[2],
        "check_hint": r[3],
        "enabled": bool(r[4]),
        "last_ok_at": r[5],
        "last_latency_ms": r[6],
        "last_error": r[7],
    }


def normalize_website_host(raw: str) -> str:
    s = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    s = s.split("/")[0].split("?")[0].strip()
    if s.startswith("www."):
        s = s[4:]
    return s


async def list_monitored_websites(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        f"SELECT {_WEBSITE_COLUMNS} FROM monitored_websites ORDER BY id"
    )
    return [_row_to_website(r) for r in rows]


async def get_monitored_website(db: Database, website_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        f"SELECT {_WEBSITE_COLUMNS} FROM monitored_websites WHERE id = ?",
        (website_id,),
    )
    if not r:
        return None
    return _row_to_website(r)


async def insert_monitored_website(
    db: Database, display_name: str, host: str, heartbeat_secret: str
) -> int:
    row = await db.write_returning_one(
        """
        INSERT INTO monitored_websites
            (display_name, host, enabled, created_at, heartbeat_secret)
        VALUES (?, ?, 1, ?, ?) RETURNING id
        """,
        (display_name.strip(), normalize_website_host(host), now_moscow_iso(), heartbeat_secret),
    )
    assert row is not None
    return int(row[0])


async def touch_website_heartbeat(db: Database, website_id: int, ip: str | None) -> None:
    await db.execute(
        """
        UPDATE monitored_websites
        SET last_heartbeat_at = ?, last_heartbeat_ip = ?
        WHERE id = ?
        """,
        (now_moscow_iso(), (ip or "")[:64] or None, website_id),
    )


async def apply_website_site_push(
    db: Database,
    website_id: int,
    *,
    host: str | None,
    resolved_ip: str | None,
    ok: bool,
    latency_ms: int | None,
    error_text: str | None,
) -> None:
    ts = now_moscow_iso()
    err_val = None if ok else ((error_text or "")[:500] or "unreachable")
    sets = [
        "last_site_ok_at = ?",
        "last_site_latency_ms = ?",
        "last_site_error = ?",
    ]
    params: list[Any] = [ts, latency_ms, err_val]
    if resolved_ip is not None:
        sets.append("last_resolved_ip = ?")
        params.append(resolved_ip[:64] or None)
    if host:
        sets.append("host = ?")
        params.append(normalize_website_host(host))
    params.append(website_id)
    await db.execute(
        f"UPDATE monitored_websites SET {', '.join(sets)} WHERE id = ?",
        tuple(params),
    )


async def regenerate_website_heartbeat_secret(
    db: Database, website_id: int, new_secret: str
) -> None:
    await db.execute(
        "UPDATE monitored_websites SET heartbeat_secret = ? WHERE id = ?",
        (new_secret, website_id),
    )


async def update_website_enabled(db: Database, website_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE monitored_websites SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, website_id),
    )


async def update_website_display_name(db: Database, website_id: int, display_name: str) -> None:
    await db.execute(
        "UPDATE monitored_websites SET display_name = ? WHERE id = ?",
        (display_name.strip(), website_id),
    )


async def delete_monitored_website(db: Database, website_id: int) -> None:
    await db.execute("DELETE FROM monitored_websites WHERE id = ?", (website_id,))


async def list_website_modules(
    db: Database, website_id: int, *, enabled_only: bool = False
) -> list[dict[str, Any]]:
    sql = f"SELECT {_MODULE_COLUMNS} FROM website_modules WHERE website_id = ?"
    params: tuple[Any, ...] = (website_id,)
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY id"
    rows = await db.fetchall(sql, params)
    return [_row_to_module(r) for r in rows]


async def get_website_module(db: Database, module_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        f"SELECT {_MODULE_COLUMNS} FROM website_modules WHERE id = ?",
        (module_id,),
    )
    if not r:
        return None
    return _row_to_module(r)


async def insert_website_module(
    db: Database,
    website_id: int,
    display_name: str,
    check_hint: str | None = None,
) -> int:
    hint = (check_hint or "").strip() or None
    row = await db.write_returning_one(
        """
        INSERT INTO website_modules (website_id, display_name, check_hint, enabled)
        VALUES (?, ?, ?, 1) RETURNING id
        """,
        (website_id, display_name.strip(), hint),
    )
    assert row is not None
    return int(row[0])


async def update_website_module_enabled(
    db: Database, module_id: int, enabled: bool
) -> None:
    await db.execute(
        "UPDATE website_modules SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, module_id),
    )


async def update_website_module_display_name(
    db: Database, module_id: int, display_name: str
) -> None:
    await db.execute(
        "UPDATE website_modules SET display_name = ? WHERE id = ?",
        (display_name.strip(), module_id),
    )


async def delete_website_module(db: Database, module_id: int) -> None:
    await db.execute("DELETE FROM website_modules WHERE id = ?", (module_id,))


async def find_website_module(
    db: Database, website_id: int, *, module_id: int | None = None
) -> dict[str, Any] | None:
    if module_id is not None:
        r = await db.fetchone(
            f"SELECT {_MODULE_COLUMNS} FROM website_modules WHERE website_id = ? AND id = ?",
            (website_id, module_id),
        )
        if r:
            return _row_to_module(r)
    return None


async def apply_website_module_push(
    db: Database,
    module_id: int,
    ok: bool,
    latency_ms: int | None,
    error_text: str | None,
) -> None:
    ts = now_moscow_iso()
    err_val = None if ok else ((error_text or "")[:500] or "unreachable")
    await db.execute(
        """
        UPDATE website_modules
        SET last_ok_at = ?, last_latency_ms = ?, last_error = ?
        WHERE id = ?
        """,
        (ts, latency_ms, err_val, module_id),
    )
    await insert_website_module_check(
        db, module_id, ok, latency_ms, error_text, check_type="module_push"
    )


async def insert_website_module_check(
    db: Database,
    module_id: int,
    ok: bool,
    latency_ms: int | None,
    error_text: str | None,
    check_type: str = "module_push",
) -> None:
    await db.execute(
        """
        INSERT INTO website_module_checks (module_id, ok, latency_ms, error_text, ts, check_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            module_id,
            1 if ok else 0,
            latency_ms,
            (error_text or "")[:500],
            now_moscow_iso(),
            check_type,
        ),
    )


async def open_website_incident(db: Database, website_id: int, last_error: str | None) -> int:
    ts = now_moscow_iso()
    row = await db.write_returning_one(
        """
        INSERT INTO website_incidents (website_id, last_error, started_at, last_alert_at)
        VALUES (?, ?, ?, ?) RETURNING id
        """,
        (website_id, (last_error or "")[:500], ts, ts),
    )
    assert row is not None
    return int(row[0])


async def get_open_website_incident(db: Database, website_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, website_id, started_at, ended_at, last_error, last_alert_at
        FROM website_incidents WHERE website_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1
        """,
        (website_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "website_id": r[1],
        "started_at": r[2],
        "ended_at": r[3],
        "last_error": r[4],
        "last_alert_at": r[5],
    }


async def update_website_incident_error(db: Database, incident_id: int, last_error: str) -> None:
    await db.execute(
        "UPDATE website_incidents SET last_error = ? WHERE id = ?",
        ((last_error or "")[:500], incident_id),
    )


async def touch_website_incident_alert(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE website_incidents SET last_alert_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def close_website_incident(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE website_incidents SET ended_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def open_website_module_incident(
    db: Database, module_id: int, last_error: str | None
) -> int:
    ts = now_moscow_iso()
    row = await db.write_returning_one(
        """
        INSERT INTO website_module_incidents (module_id, last_error, started_at, last_alert_at)
        VALUES (?, ?, ?, ?) RETURNING id
        """,
        (module_id, (last_error or "")[:500], ts, ts),
    )
    assert row is not None
    return int(row[0])


async def get_open_website_module_incident(
    db: Database, module_id: int
) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, module_id, started_at, ended_at, last_error, last_alert_at
        FROM website_module_incidents WHERE module_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1
        """,
        (module_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "module_id": r[1],
        "started_at": r[2],
        "ended_at": r[3],
        "last_error": r[4],
        "last_alert_at": r[5],
    }


async def update_website_module_incident_error(
    db: Database, incident_id: int, last_error: str
) -> None:
    await db.execute(
        "UPDATE website_module_incidents SET last_error = ? WHERE id = ?",
        ((last_error or "")[:500], incident_id),
    )


async def touch_website_module_incident_alert(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE website_module_incidents SET last_alert_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


async def close_website_module_incident(db: Database, incident_id: int) -> None:
    await db.execute(
        "UPDATE website_module_incidents SET ended_at = ? WHERE id = ?",
        (now_moscow_iso(), incident_id),
    )


EXPORT_WEBSITE_MODULE_CHECKS_LIMIT = 200_000


async def export_website_module_checks_for_report(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_WEBSITE_MODULE_CHECKS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT c.id, c.module_id, m.display_name, w.display_name, w.host,
               c.ts, c.ok, c.latency_ms, c.error_text, c.check_type
        FROM website_module_checks c
        JOIN website_modules m ON m.id = c.module_id
        JOIN monitored_websites w ON w.id = m.website_id
        WHERE c.ts >= ? AND c.ts <= ?
        ORDER BY c.ts ASC
        LIMIT ?
        """,
        (start_iso, end_iso, lim),
    )
    truncated = len(rows) > EXPORT_WEBSITE_MODULE_CHECKS_LIMIT
    if truncated:
        rows = rows[:EXPORT_WEBSITE_MODULE_CHECKS_LIMIT]
    out = [
        {
            "id": r[0],
            "module_id": r[1],
            "module_name": r[2],
            "website_name": r[3],
            "host": r[4],
            "ts": r[5],
            "ok": bool(r[6]),
            "latency_ms": r[7],
            "error_text": r[8] or "",
            "check_type": r[9] or "module_push",
        }
        for r in rows
    ]
    return out, truncated


async def export_website_incidents_overlapping(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_INCIDENTS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT i.id, i.website_id, w.display_name, w.host, i.started_at, i.ended_at,
               i.last_error, i.last_alert_at
        FROM website_incidents i
        JOIN monitored_websites w ON w.id = i.website_id
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
            "website_id": r[1],
            "display_name": r[2],
            "host": r[3],
            "started_at": r[4],
            "ended_at": r[5],
            "last_error": r[6] or "",
            "last_alert_at": r[7],
        }
        for r in rows
    ]
    return out, truncated


async def export_website_module_incidents_overlapping(
    db: Database,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    lim = EXPORT_INCIDENTS_LIMIT + 1
    rows = await db.fetchall(
        """
        SELECT i.id, i.module_id, m.display_name, w.display_name, w.host,
               i.started_at, i.ended_at, i.last_error, i.last_alert_at
        FROM website_module_incidents i
        JOIN website_modules m ON m.id = i.module_id
        JOIN monitored_websites w ON w.id = m.website_id
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
            "module_id": r[1],
            "module_name": r[2],
            "website_name": r[3],
            "host": r[4],
            "started_at": r[5],
            "ended_at": r[6],
            "last_error": r[7] or "",
            "last_alert_at": r[8],
        }
        for r in rows
    ]
    return out, truncated


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


def _parse_panel_ids_json(raw: str | None) -> list[int]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [int(x) for x in data if isinstance(x, (int, float, str)) and str(x).isdigit()]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


async def load_admin_chat_ui(db: Database, chat_id: int) -> tuple[int | None, list[int]]:
    row = await db.fetchone(
        "SELECT panel_message_id, panel_ids FROM admin_chat_ui WHERE chat_id = ?",
        (chat_id,),
    )
    if not row:
        return None, []
    mid = int(row[0]) if row[0] is not None else None
    return mid, _parse_panel_ids_json(str(row[1]) if row[1] is not None else "[]")


async def save_admin_chat_ui(
    db: Database,
    chat_id: int,
    *,
    panel_message_id: int | None,
    panel_ids: list[int],
) -> None:
    unique_ids = list(dict.fromkeys(panel_ids))
    await db.execute(
        """
        INSERT INTO admin_chat_ui (chat_id, panel_message_id, panel_ids)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            panel_message_id = excluded.panel_message_id,
            panel_ids = excluded.panel_ids
        """,
        (chat_id, panel_message_id, json.dumps(unique_ids)),
    )
