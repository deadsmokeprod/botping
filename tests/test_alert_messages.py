from __future__ import annotations

from botping.monitor.alert_messages import (
    alert_down,
    alert_recover,
    alert_router_event,
)


def test_alert_down_prefix():
    assert alert_down("Недоступен: test").startswith("🔴")


def test_alert_recover_prefix():
    assert alert_recover("Восстановлено: test").startswith("✅")


def test_alert_router_lte():
    msg = alert_router_event("«Office»: LTE", "internet_lte")
    assert "📶" in msg
    assert "🔴" in msg
