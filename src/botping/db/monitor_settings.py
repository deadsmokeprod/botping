from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Ключи, которые можно переопределить на сущности (бот / роутер / LAN / сайт / модуль).
MONITOR_OVERRIDE_KEYS: tuple[str, ...] = (
    "heartbeat_timeout_sec",
    "fail_threshold",
    "recover_threshold",
    "down_alert_sec",
    "repeat_alert_interval_sec",
    "slow_ms",
    "quiet_hours",
)

ENTITY_KINDS: tuple[str, ...] = ("bot", "router", "target", "website", "module")

_ENTITY_TABLE: dict[str, tuple[str, str]] = {
    "bot": ("monitored_bots", "id"),
    "router": ("monitored_routers", "id"),
    "target": ("router_targets", "id"),
    "website": ("monitored_websites", "id"),
    "module": ("website_modules", "id"),
}


def parse_settings_override(raw: str | None) -> dict[str, str]:
    if not raw or not str(raw).strip():
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, str] = {}
    for k in MONITOR_OVERRIDE_KEYS:
        if k in obj and obj[k] is not None:
            out[k] = str(obj[k])
    return out


def merge_settings_dict(base: dict[str, str], override: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    merged.update(override)
    return merged


@dataclass(frozen=True)
class EffectiveMonitorSettings:
    heartbeat_timeout_sec: int
    fail_threshold: int
    recover_threshold: int
    down_alert_sec: int
    repeat_alert_interval_sec: int
    slow_ms: int
    quiet_hours: dict[str, Any]


def resolve_monitor_settings_chain(
    global_parsed: dict[str, Any],
    *override_layers: str | None,
) -> EffectiveMonitorSettings:
    """Глобальные → слои override (например сайт, затем модуль). Поздний слой перекрывает ранний."""
    merged_ov: dict[str, str] = {}
    for raw in override_layers:
        merged_ov.update(parse_settings_override(raw))
    payload = json.dumps(merged_ov, ensure_ascii=False) if merged_ov else None
    return resolve_monitor_settings(global_parsed, payload)


def resolve_monitor_settings(
    global_parsed: dict[str, Any],
    override_raw: str | None,
) -> EffectiveMonitorSettings:
    """Слияние глобальных настроек (уже распарсенных) с JSON override сущности."""
    ov = parse_settings_override(override_raw)
    merged: dict[str, Any] = dict(global_parsed)
    for k, v in ov.items():
        if k == "quiet_hours":
            try:
                merged[k] = json.loads(v) if v else {}
            except json.JSONDecodeError:
                merged[k] = global_parsed.get("quiet_hours") or {}
        elif k in (
            "heartbeat_timeout_sec",
            "fail_threshold",
            "recover_threshold",
            "down_alert_sec",
            "repeat_alert_interval_sec",
            "slow_ms",
        ):
            merged[k] = int(v)

    hb = max(30, int(merged.get("heartbeat_timeout_sec", 120)))
    fail = max(1, int(merged.get("fail_threshold", 2)))
    recover = max(1, int(merged.get("recover_threshold", 2)))
    down_alert = max(0, int(merged.get("down_alert_sec", 0)))
    repeat = max(60, int(merged.get("repeat_alert_interval_sec", 3600)))
    slow = max(0, int(merged.get("slow_ms", 0)))
    qh = merged.get("quiet_hours")
    if not isinstance(qh, dict):
        qh = {}

    return EffectiveMonitorSettings(
        heartbeat_timeout_sec=hb,
        fail_threshold=fail,
        recover_threshold=recover,
        down_alert_sec=down_alert,
        repeat_alert_interval_sec=repeat,
        slow_ms=slow,
        quiet_hours=qh,
    )


def entity_table(kind: str) -> tuple[str, str]:
    if kind not in _ENTITY_TABLE:
        raise ValueError(f"unknown entity kind: {kind}")
    return _ENTITY_TABLE[kind]
