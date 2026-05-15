from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


MAX_CHECKS_PER_PAYLOAD = 64
MAX_ERROR_LEN = 200


@dataclass(frozen=True)
class ParsedCheckItem:
    target_id: int | None
    address: str | None
    ok: bool
    latency_ms: int | None
    error: str | None


@dataclass(frozen=True)
class ParsedHeartbeatPayload:
    checks: list[ParsedCheckItem]


def parse_heartbeat_payload(body: bytes | None) -> ParsedHeartbeatPayload | None:
    """None = пустое тело / не JSON (только touch роутера)."""
    if not body:
        return None
    raw = body.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(body, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        raise ValueError("invalid_json")
    if not isinstance(data, dict):
        raise ValueError("invalid_json")
    checks_raw = data.get("checks")
    if checks_raw is None:
        return ParsedHeartbeatPayload(checks=[])
    if not isinstance(checks_raw, list):
        raise ValueError("invalid_checks")
    if len(checks_raw) > MAX_CHECKS_PER_PAYLOAD:
        raise ValueError("too_many_checks")
    out: list[ParsedCheckItem] = []
    for item in checks_raw:
        if not isinstance(item, dict):
            continue
        if "ok" not in item:
            continue
        ok = item["ok"]
        if not isinstance(ok, bool):
            if isinstance(ok, int):
                ok = ok != 0
            else:
                continue
        tid: int | None = None
        if "id" in item and item["id"] is not None:
            try:
                tid = int(item["id"])
            except (TypeError, ValueError):
                pass
        addr: str | None = None
        if "address" in item and item["address"] is not None:
            addr = str(item["address"]).strip() or None
        if tid is None and not addr:
            continue
        ms: int | None = None
        if "ms" in item and item["ms"] is not None:
            try:
                ms_val = int(item["ms"])
                if ms_val >= 0:
                    ms = ms_val
            except (TypeError, ValueError):
                pass
        err: str | None = None
        if "error" in item and item["error"] is not None:
            err = str(item["error"])[:MAX_ERROR_LEN] or None
        out.append(
            ParsedCheckItem(
                target_id=tid,
                address=addr,
                ok=ok,
                latency_ms=ms,
                error=err,
            )
        )
    return ParsedHeartbeatPayload(checks=out)
