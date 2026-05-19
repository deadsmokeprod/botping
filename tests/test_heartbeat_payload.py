from __future__ import annotations

import json
import unittest

from botping.heartbeat_payload import parse_heartbeat_payload
from botping.router_events import format_router_event_message


class TestHeartbeatPayload(unittest.TestCase):
    def test_events_only(self) -> None:
        body = json.dumps({"events": [{"type": "internet_lte"}]}).encode()
        p = parse_heartbeat_payload(body)
        assert p is not None
        self.assertEqual(len(p.events), 1)
        self.assertEqual(p.events[0].event_type, "internet_lte")
        self.assertEqual(p.checks, [])
        self.assertIsNone(p.site)

    def test_checks_and_events(self) -> None:
        body = json.dumps(
            {
                "checks": [{"id": 1, "address": "10.0.0.1", "ok": True, "ms": 5}],
                "events": [{"type": "internet_wan"}],
            }
        ).encode()
        p = parse_heartbeat_payload(body)
        assert p is not None
        self.assertEqual(len(p.checks), 1)
        self.assertEqual(len(p.events), 1)

    def test_custom_requires_text(self) -> None:
        body = json.dumps({"events": [{"type": "custom"}]}).encode()
        p = parse_heartbeat_payload(body)
        assert p is not None
        self.assertEqual(p.events, [])

    def test_custom_with_text(self) -> None:
        body = json.dumps({"events": [{"type": "custom", "text": "Тест"}]}).encode()
        p = parse_heartbeat_payload(body)
        assert p is not None
        self.assertEqual(len(p.events), 1)
        self.assertEqual(p.events[0].custom_text, "Тест")

    def test_invalid_events_type(self) -> None:
        body = json.dumps({"events": "x"}).encode()
        with self.assertRaises(ValueError):
            parse_heartbeat_payload(body)

    def test_format_messages_ru(self) -> None:
        self.assertIn("LTE", format_router_event_message("Офис", "internet_lte"))
        self.assertIn("WAN", format_router_event_message("Офис", "internet_wan"))


if __name__ == "__main__":
    unittest.main()
