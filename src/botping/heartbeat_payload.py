from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from botping.router_events import ALLOWED_EVENT_TYPES, MAX_CUSTOM_TEXT_LEN


MAX_CHECKS_PER_PAYLOAD = 64
MAX_EVENTS_PER_PAYLOAD = 5
MAX_ERROR_LEN = 200


@dataclass(frozen=True)
class ParsedCheckItem:
    target_id: int | None
    address: str | None
    ok: bool
    latency_ms: int | None
    error: str | None


@dataclass(frozen=True)
class ParsedEventItem:
    event_type: str
    custom_text: str | None


@dataclass(frozen=True)
class ParsedHeartbeatPayload:
    checks: list[ParsedCheckItem]
    events: list[ParsedEventItem]


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

    checks = _parse_checks(data.get("checks"))
    events = _parse_events(data.get("events"))

    if not checks and not events and data.get("checks") is None and data.get("events") is None:
        return None
    return ParsedHeartbeatPayload(checks=checks, events=events)


def _parse_checks(checks_raw: Any) -> list[ParsedCheckItem]:
    if checks_raw is None:
        return []
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
    return out


def _parse_events(events_raw: Any) -> list[ParsedEventItem]:
    if events_raw is None:
        return []
    if not isinstance(events_raw, list):
        raise ValueError("invalid_events")
    if len(events_raw) > MAX_EVENTS_PER_PAYLOAD:
        raise ValueError("too_many_events")
    out: list[ParsedEventItem] = []
    for item in events_raw:
        if not isinstance(item, dict):
            continue
        et = item.get("type")
        if et is None:
            continue
        event_type = str(et).strip().lower()
        if event_type not in ALLOWED_EVENT_TYPES:
            continue
        custom_text: str | None = None
        if event_type == "custom":
            raw_text = item.get("text")
            if raw_text is None:
                continue
            custom_text = str(raw_text).strip()[:MAX_CUSTOM_TEXT_LEN]
            if not custom_text:
                continue
        out.append(ParsedEventItem(event_type=event_type, custom_text=custom_text))
    return out
