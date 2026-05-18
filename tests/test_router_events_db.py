from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from botping.db.pool import Database, generate_heartbeat_secret
from botping.db import queries


class TestRouterEventsDb(unittest.TestCase):
    def test_dedup_within_two_minutes(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as td:
                db = Database(str(Path(td) / "t.db"))
                await db.connect()
                rid = await queries.insert_monitored_router(
                    db, "Тест", generate_heartbeat_secret()
                )
                eid1, n1, _ = await queries.record_router_event(
                    db, rid, "Тест", "internet_lte", "1.2.3.4"
                )
                self.assertIsNotNone(eid1)
                self.assertTrue(n1)
                eid2, n2, _ = await queries.record_router_event(
                    db, rid, "Тест", "internet_lte", "1.2.3.4"
                )
                self.assertIsNone(eid2)
                self.assertFalse(n2)
                eid3, n3, _ = await queries.record_router_event(
                    db, rid, "Тест", "internet_wan", "1.2.3.4"
                )
                self.assertIsNotNone(eid3)
                self.assertTrue(n3)
                latest = await queries.get_latest_router_event(db, rid)
                assert latest is not None
                self.assertEqual(latest["event_type"], "internet_wan")
                await db.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
