from __future__ import annotations

import json

from botping.db.monitor_settings import resolve_monitor_settings


def test_resolve_monitor_settings_override():
    global_parsed = {
        "heartbeat_timeout_sec": 120,
        "fail_threshold": 2,
        "recover_threshold": 2,
        "down_alert_sec": 0,
        "repeat_alert_interval_sec": 3600,
        "slow_ms": 0,
        "quiet_hours": {},
    }
    override = json.dumps({"fail_threshold": "5", "down_alert_sec": "600"})
    eff = resolve_monitor_settings(global_parsed, override)
    assert eff.fail_threshold == 5
    assert eff.down_alert_sec == 600
    assert eff.heartbeat_timeout_sec == 120


def test_resolve_monitor_settings_empty_override():
    global_parsed = {
        "heartbeat_timeout_sec": 90,
        "fail_threshold": 3,
        "recover_threshold": 2,
        "down_alert_sec": 10,
        "repeat_alert_interval_sec": 1800,
        "slow_ms": 5000,
        "quiet_hours": {"start": "22:00", "end": "07:00", "tz": "Europe/Moscow"},
    }
    eff = resolve_monitor_settings(global_parsed, None)
    assert eff.heartbeat_timeout_sec == 90
    assert eff.slow_ms == 5000
