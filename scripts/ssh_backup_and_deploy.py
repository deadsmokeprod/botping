"""Backup remote SQLite DB via SSH, then git pull + docker compose rebuild."""
from __future__ import annotations

import os
import sys
from datetime import datetime
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

ts = datetime.now().strftime("%Y%m%d-%H%M%S")
local_backup = ROOT / "backups" / f"botping-{ts}.db"
remote_tmp = f"/tmp/botping-backup-{ts}.db"

local_backup.parent.mkdir(parents=True, exist_ok=True)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print(f"=== SSH {user}@{host}:{port} ===")
client.connect(
    host,
    port=port,
    username=user,
    password=password,
    timeout=30,
    allow_agent=False,
    look_for_keys=False,
)


def run(cmd: str, timeout: int = 300) -> tuple[int, str, str]:
    print(f"\n$ {cmd}")
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = int(stdout.channel.recv_exit_status())
    if out.strip():
        print(out.rstrip().encode("utf-8", errors="replace").decode("utf-8"))
    if err.strip():
        print("[stderr]", err.rstrip().encode("utf-8", errors="replace").decode("utf-8"))
    return code, out, err


# 1) Backup inside container (sqlite .backup if possible, else cp)
backup_cmd = f"""
set -e
cd {path}
CID=$(docker compose ps -q botping)
if [ -z "$CID" ]; then echo "ERROR: botping container not running"; exit 1; fi
echo "Container: $CID"
docker compose exec -T botping sh -c 'ls -la /app/data/'
docker compose exec -T botping sh -c '
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 /app/data/botping.db ".backup /app/data/_backup_tmp.db"
    cp /app/data/_backup_tmp.db /app/data/_backup_export.db
  else
    cp /app/data/botping.db /app/data/_backup_export.db
  fi
  ls -la /app/data/_backup_export.db
'
docker cp "$CID:/app/data/_backup_export.db" {remote_tmp}
ls -lh {remote_tmp}
"""
code, _, _ = run(backup_cmd, timeout=120)
if code != 0:
    print("BACKUP FAILED on server")
    client.close()
    sys.exit(2)

# 2) Download via SFTP
print(f"\n--- SFTP get -> {local_backup} ---")
sftp = client.open_sftp()
sftp.get(remote_tmp, str(local_backup))
stat = local_backup.stat()
print(f"Downloaded: {local_backup} ({stat.st_size} bytes)")
sftp.remove(remote_tmp)
sftp.close()

# 3) Update
print("\n=== DEPLOY: git pull + docker compose up -d --build ===")
code, out, err = run(f"cd {path} && git fetch origin main && git pull origin main", timeout=180)
if code != 0:
    print("git pull failed — check remote credentials on server")
    client.close()
    sys.exit(3)

run(f"cd {path} && docker compose up -d --build", timeout=600)
run(f"cd {path} && git log -1 --oneline && docker compose ps", timeout=60)
run(f"cd {path} && docker compose logs --tail=20", timeout=60)

client.close()
print(f"\n=== Done. Local backup: {local_backup} ===")
