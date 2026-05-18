"""VPS Botping DB + .env audit via SSH (reads DEPLOY_* from .env)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

import paramiko  # noqa: E402

host = os.environ.get("DEPLOY_SSH_HOST", "").strip()
user = os.environ.get("DEPLOY_SSH_USER", "root").strip()
password = os.environ.get("DEPLOY_SSH_PASSWORD", "")
port = int(os.environ.get("DEPLOY_SSH_PORT", "22") or 22)
path = os.environ.get("DEPLOY_PATH", "/opt/botping").strip()

if not host or not password:
    print("ERROR: DEPLOY_SSH_HOST or DEPLOY_SSH_PASSWORD missing in .env")
    sys.exit(1)

REMOTE_SQL = r"""
import sqlite3, json
c = sqlite3.connect("/app/data/botping.db")
c.row_factory = sqlite3.Row
out = {}
out["routers"] = [dict(r) for r in c.execute(
    "SELECT id, display_name, enabled, last_heartbeat_at, last_heartbeat_ip, "
    "length(heartbeat_secret) AS secret_len FROM monitored_routers ORDER BY id"
).fetchall()]
out["targets"] = [dict(r) for r in c.execute(
    "SELECT id, router_id, display_name, address, enabled, last_ok_at, "
    "last_latency_ms, last_error FROM router_targets ORDER BY router_id, id"
).fetchall()]
out["open_router_incidents"] = [dict(r) for r in c.execute(
    "SELECT id, router_id, started_at, last_error FROM router_incidents WHERE ended_at IS NULL"
).fetchall()]
out["open_target_incidents"] = [dict(r) for r in c.execute(
    "SELECT id, target_id, started_at, last_error FROM router_target_incidents WHERE ended_at IS NULL"
).fetchall()]
print(json.dumps(out, ensure_ascii=False))
"""

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print(f"=== DB check {user}@{host}:{port} ===")
client.connect(
    host,
    port=port,
    username=user,
    password=password,
    timeout=30,
    allow_agent=False,
    look_for_keys=False,
)


def run(title: str, cmd: str) -> None:
    print(f"\n--- {title} ---")
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=90)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("[stderr]", err.rstrip())


run(
    "env (public host / port / admins)",
    f"grep -E '^(BOTPING_PUBLIC_|HEARTBEAT_PORT|ADMIN_CHAT)' {path}/.env 2>/dev/null || true",
)
run(
    "git version on server",
    f"cd {path} && git log -1 --oneline 2>/dev/null",
)
import base64

b64 = base64.b64encode(REMOTE_SQL.encode()).decode()
run(
    "database",
    f"cd {path} && docker compose exec -T botping python -c "
    f"\"import base64; exec(base64.b64decode('{b64}').decode())\"",
)
client.close()
print("\n=== done ===")
