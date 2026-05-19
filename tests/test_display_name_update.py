from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from botping.db.pool import Database, generate_heartbeat_secret
from botping.db import queries


class TestDisplayNameUpdate(unittest.TestCase):
    def test_rename_all_kinds_preserves_host_and_address(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as td:
                db = Database(os.path.join(td, "t.db"))
                await db.connect()
                bot_secret = generate_heartbeat_secret()
                bid = await queries.insert_monitored_bot(
                    db, "BotOld", "123:ABC", bot_secret
                )
                router_secret = generate_heartbeat_secret()
                rid = await queries.insert_monitored_router(db, "RouterOld", router_secret)
                tid = await queries.insert_router_target(
                    db, rid, "CamOld", "192.168.1.10"
                )
                web_secret = generate_heartbeat_secret()
                wid = await queries.insert_monitored_website(
                    db, "SiteOld", "example.com", web_secret
                )
                mid = await queries.insert_website_module(db, wid, "ModOld", "/api/")

                await queries.update_bot_display_name(db, bid, "BotNew")
                await queries.update_router_display_name(db, rid, "RouterNew")
                await queries.update_router_target_display_name(db, tid, "CamNew")
                await queries.update_website_display_name(db, wid, "SiteNew")
                await queries.update_website_module_display_name(db, mid, "ModNew")

                b = await queries.get_monitored_bot(db, bid)
                r = await queries.get_monitored_router(db, rid)
                t = await queries.get_router_target(db, tid)
                w = await queries.get_monitored_website(db, wid)
                m = await queries.get_website_module(db, mid)
                assert b is not None
                assert r is not None
                assert t is not None
                assert w is not None
                assert m is not None

                self.assertEqual(b["display_name"], "BotNew")
                self.assertEqual(r["display_name"], "RouterNew")
                self.assertEqual(t["display_name"], "CamNew")
                self.assertEqual(t["address"], "192.168.1.10")
                self.assertEqual(w["display_name"], "SiteNew")
                self.assertEqual(w["host"], "example.com")
                self.assertEqual(m["display_name"], "ModNew")
                self.assertEqual(m.get("check_hint"), "/api/")
                await db.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
