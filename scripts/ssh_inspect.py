"""One-off VPS inspect via DEPLOY_SSH_* from .env (do not commit)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import paramiko  # noqa: E402

host = os.environ.get("DEPLOY_SSH_HOST", "").strip()
user = os.environ.get("DEPLOY_SSH_USER", "root").strip()
password = os.environ.get("DEPLOY_SSH_PASSWORD", "")
port = int(os.environ.get("DEPLOY_SSH_PORT", "22") or 22)
path = os.environ.get("DEPLOY_PATH", "/opt/botping").strip()

if not host or not password:
    print("ERROR: DEPLOY_SSH_HOST or DEPLOY_SSH_PASSWORD missing in .env")
    sys.exit(1)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print(f"=== SSH connect {user}@{host}:{port} ===")
try:
    client.connect(
        host,
        port=port,
        username=user,
        password=password,
        timeout=20,
        allow_agent=False,
        look_for_keys=False,
    )
except Exception as e:
    print(f"CONNECT FAILED: {e}")
    sys.exit(2)


def run(title: str, cmd: str) -> int:
    print(f"\n--- {title} ---")
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=90)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("[stderr]", err.rstrip())
    return int(stdout.channel.recv_exit_status())


run("hostname / uptime", "hostname; uptime")
run("project dir", f"ls -la {path} 2>&1 | head -20")
run(
    "git",
    f"cd {path} && git rev-parse --short HEAD 2>/dev/null; "
    "git log -1 --oneline 2>/dev/null; git status -sb 2>/dev/null",
)
run("docker compose ps", f"cd {path} && docker compose ps 2>&1")
run("docker logs (last 25 lines)", f"cd {path} && docker compose logs --tail=25 2>&1")
client.close()
print("\n=== SSH session closed ===")
