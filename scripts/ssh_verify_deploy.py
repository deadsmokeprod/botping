"""Quick post-deploy check on VPS."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import paramiko

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

host = os.environ.get("DEPLOY_SSH_HOST", "").strip()
user = os.environ.get("DEPLOY_SSH_USER", "root").strip()
password = os.environ.get("DEPLOY_SSH_PASSWORD", "")
path = os.environ.get("DEPLOY_PATH", "/opt/botping").strip()

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    host,
    username=user,
    password=password,
    timeout=30,
    allow_agent=False,
    look_for_keys=False,
)

cmds = [
    f"cd {path} && git log -1 --oneline",
    f"cd {path} && docker compose ps",
    f'cd {path} && docker compose exec -T botping python -c "import botping.router_events; print(botping.router_events.ALLOWED_EVENT_TYPES)"',
    f'cd {path} && docker compose exec -T botping python -c "import sqlite3; c=sqlite3.connect(\\"/app/data/botping.db\\"); print(c.execute(\\"SELECT name FROM sqlite_master WHERE name=\'router_events\'\\").fetchall())"',
]

for cmd in cmds:
    print(f"\n$ {cmd}")
    _, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out:
        sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
    if err.strip():
        sys.stdout.buffer.write(f"[stderr] {err}".encode("utf-8", errors="replace"))
    print(f"exit {code}")

client.close()
