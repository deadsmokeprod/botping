from __future__ import annotations

from typing import Any


def build_site_agent_snippet(
    url: str,
    secret: str,
    host: str,
    modules: list[dict[str, Any]],
    *,
    timeout_sec: float = 15.0,
) -> str:
    """Генерирует Python-скрипт агента для cron на сервере сайта."""
    base_url = url.rstrip("/")
    hb_url = base_url if base_url.endswith("/heartbeat") else f"{base_url}/heartbeat"
    host_esc = host.replace("\\", "\\\\").replace('"', '\\"')
    secret_esc = secret.replace("\\", "\\\\").replace('"', '\\"')
    hb_esc = hb_url.replace("\\", "\\\\").replace('"', '\\"')

    module_defs: list[str] = []
    module_calls: list[str] = []
    for m in modules:
        mid = int(m["id"])
        name = str(m.get("display_name") or f"module_{mid}")
        hint = (m.get("check_hint") or "").strip()
        fn = f"check_module_{mid}"
        if hint:
            hint_esc = hint.replace("\\", "\\\\").replace('"', '\\"')
            module_defs.append(
                f'''
def {fn}() -> tuple[bool, int | None, str | None]:
    """{name}"""
    return _http_get_check("{hint_esc}")
'''
            )
        else:
            module_defs.append(
                f'''
def {fn}() -> tuple[bool, int | None, str | None]:
    """{name} — настройте проверку (форма, API)."""
    # Пример: return _http_get_check("https://{host_esc}/api/health")
    return False, None, "not_configured"
'''
            )
        module_calls.append(
            f'    results.append(_run_check({mid}, "{name}", {fn}))'
        )

    modules_block = "\n".join(module_defs) if module_defs else ""
    calls_block = "\n".join(module_calls) if module_calls else "    pass  # нет модулей"

    return f'''#!/usr/bin/env python3
"""Botping site agent — cron каждые 30 с."""
from __future__ import annotations

import json
import socket
import sys
import time

try:
    import httpx
except ImportError:
    print("pip install httpx", file=sys.stderr)
    sys.exit(1)

BOTPING_URL = "{hb_esc}"
HEARTBEAT_SECRET = "{secret_esc}"
HOST = "{host_esc}"
TIMEOUT = {timeout_sec}


def _resolve_ip(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except OSError:
        pass
    return None


def _http_get_check(url_or_path: str) -> tuple[bool, int | None, str | None]:
    url = url_or_path if "://" in url_or_path else f"https://{{HOST}}{{url_or_path}}"
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            r = client.get(url)
        ms = int((time.perf_counter() - t0) * 1000)
        if 200 <= r.status_code < 400:
            return True, ms, None
        return False, ms, f"http_{{r.status_code}}"
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return False, ms, str(e)[:200]


def check_site() -> tuple[bool, int | None, str | None, str | None]:
    ip = _resolve_ip(HOST)
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            r = client.get(f"https://{{HOST}}/")
        ms = int((time.perf_counter() - t0) * 1000)
        ok = 200 <= r.status_code < 400
        err = None if ok else f"http_{{r.status_code}}"
        return ok, ms, err, ip
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return False, ms, str(e)[:200], ip
{modules_block}

def _run_check(mid: int, _name: str, fn) -> dict:
    ok, ms, err = fn()
    item = {{"id": mid, "ok": ok}}
    if ms is not None:
        item["ms"] = ms
    if err:
        item["error"] = err
    return item


def main() -> None:
    ok, ms, err, ip = check_site()
    payload: dict = {{
        "site": {{
            "host": HOST,
            "ip": ip,
            "ok": ok,
            "ms": ms,
            "error": err,
        }},
        "checks": [],
    }}
    results: list[dict] = []
{calls_block}
    payload["checks"] = results
    headers = {{
        "X-Heartbeat-Secret": HEARTBEAT_SECRET,
        "Content-Type": "application/json",
    }}
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(BOTPING_URL, headers=headers, content=json.dumps(payload))
    if r.status_code >= 400:
        print(f"heartbeat failed: {{r.status_code}} {{r.text[:200]}}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
'''
