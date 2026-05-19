from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest

from botping.db.pool import Database, generate_heartbeat_secret
from botping.db import queries
from botping.heartbeat_payload import parse_heartbeat_payload


class TestWebsitePayloadDb(unittest.TestCase):
    def test_parse_site_block(self) -> None:
        body = json.dumps(
            {
                "site": {
                    "host": "mongol.pro",
                    "ip": "1.2.3.4",
                    "ok": True,
                    "ms": 42,
                },
                "checks": [{"id": 1, "ok": False, "error": "form timeout"}],
            }
        ).encode()
        p = parse_heartbeat_payload(body)
        assert p is not None
        self.assertIsNotNone(p.site)
        assert p.site is not None
        self.assertEqual(p.site.host, "mongol.pro")
        self.assertEqual(p.site.ip, "1.2.3.4")
        self.assertTrue(p.site.ok)
        self.assertEqual(p.site.latency_ms, 42)
        self.assertEqual(len(p.checks), 1)
        self.assertEqual(p.checks[0].target_id, 1)

    def test_website_crud_and_push(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as td:
                db = Database(os.path.join(td, "t.db"))
                await db.connect()
                secret = generate_heartbeat_secret()
                wid = await queries.insert_monitored_website(
                    db, "Mongol", "mongol.pro", secret
                )
                mid = await queries.insert_website_module(
                    db, wid, "Форма", "/contact/"
                )
                await queries.touch_website_heartbeat(db, wid, "10.0.0.1")
                await queries.apply_website_site_push(
                    db,
                    wid,
                    host="mongol.pro",
                    resolved_ip="93.184.216.34",
                    ok=True,
                    latency_ms=10,
                    error_text=None,
                )
                await queries.apply_website_module_push(
                    db, mid, True, 50, None
                )
                w = await queries.get_monitored_website(db, wid)
                assert w is not None
                self.assertEqual(w["last_resolved_ip"], "93.184.216.34")
                m = await queries.get_website_module(db, mid)
                assert m is not None
                self.assertIsNotNone(m["last_ok_at"])
                cache = await queries.list_heartbeat_secrets_for_cache(db)
                kinds = [k for k, eid, s in cache if eid == wid]
                self.assertIn("website", kinds)
                await db.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
