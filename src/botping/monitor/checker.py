from __future__ import annotations

import time
from dataclasses import dataclass
import httpx


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    latency_ms: int
    http_status: int | None
    error_text: str | None
    rate_limited: bool


async def check_getme(client: httpx.AsyncClient, token: str, timeout_sec: float) -> CheckResult:
    url = f"https://api.telegram.org/bot{token}/getMe"
    t0 = time.perf_counter()
    try:
        resp = await client.get(url, timeout=timeout_sec)
    except httpx.TimeoutException:
        ms = int((time.perf_counter() - t0) * 1000)
        return CheckResult(False, ms, None, "timeout", False)
    except httpx.RequestError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return CheckResult(False, ms, None, str(e.__class__.__name__), False)

    ms = int((time.perf_counter() - t0) * 1000)
    try:
        data = resp.json()
    except ValueError:
        return CheckResult(False, ms, resp.status_code, "invalid_json", False)

    if not isinstance(data, dict):
        return CheckResult(False, ms, resp.status_code, "invalid_json", False)

    if data.get("ok") is True:
        return CheckResult(True, ms, resp.status_code, None, False)

    err = str(data.get("description") or "telegram_error")
    code = data.get("error_code")
    if code == 429:
        return CheckResult(False, ms, resp.status_code, err, True)
    return CheckResult(False, ms, resp.status_code, err, False)
