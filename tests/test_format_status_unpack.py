from __future__ import annotations

import pytest

from botping.monitor.router_monitor import _target_alive
from botping.monitor.website_monitor import _module_alive


def test_target_alive_returns_three():
    t = {"last_ok_at": None}
    result = _target_alive(t, 120)
    assert len(result) == 3


def test_module_alive_returns_three():
    m = {"last_ok_at": None}
    result = _module_alive(m, 120)
    assert len(result) == 3
