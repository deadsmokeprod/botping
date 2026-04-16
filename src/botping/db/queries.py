from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import aiosqlite

from botping.timeutil import now_moscow_iso

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


async def list_monitored_bots(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT id, display_name, token, enabled, created_at FROM monitored_bots ORDER BY id"
    )
    return [
        {
            "id": r[0],
            "display_name": r[1],
            "token": r[2],
            "enabled": bool(r[3]),
            "created_at": r[4],
        }
        for r in rows
    ]


async def get_monitored_bot(db: Database, bot_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        "SELECT id, display_name, token, enabled, created_at FROM monitored_bots WHERE id = ?",
        (bot_id,),
    )
    if not r:
        return None
    return {
        "id": r[0],
        "display_name": r[1],
        "token": r[2],
        "enabled": bool(r[3]),
        "created_at": r[4],
    }


async def insert_monitored_bot(db: Database, display_name: str, token: str) -> int:
    row = await db.write_returning_one(
        "INSERT INTO monitored_bots (display_name, token, enabled, created_at) VALUES (?, ?, 1, ?) RETURNING id",
        (display_name.strip(), token.strip(), now_moscow_iso()),
    )
    assert row is not None
    return int(row[0])


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
) -> None:
    await db.execute(
        """
        INSERT INTO checks (bot_id, ok, latency_ms, http_status, error_text, rate_limited, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bot_id,
            1 if ok else 0,
            latency_ms,
            http_status,
            (error_text or "")[:500],
            1 if rate_limited else 0,
            now_moscow_iso(),
        ),
    )


async def get_last_check(db: Database, bot_id: int) -> dict[str, Any] | None:
    r = await db.fetchone(
        """
        SELECT id, bot_id, ts, ok, latency_ms, http_status, error_text, rate_limited
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
        SELECT c.id, c.bot_id, b.display_name, c.ts, c.ok, c.latency_ms, c.http_status, c.error_text, c.rate_limited
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
    return out


async def load_all_settings(db: Database) -> dict[str, Any]:
    rows = await db.fetchall("SELECT key, value FROM settings")
    merged = dict(DEFAULT_SETTINGS)
    merged.update({str(r[0]): str(r[1]) for r in rows})
    return parse_settings_row(merged)


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
