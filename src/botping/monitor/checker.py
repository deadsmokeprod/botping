from __future__ import annotations

import time
from dataclasses import dataclass
import httpx


@dataclass(frozen=True)
class CheckResult:
    """Результат getMe-проверки (sanity-check токена при добавлении бота
    и отдельная проверка доступности Telegram API с VPS Botping)."""

    ok: bool
    latency_ms: int
    http_status: int | None
    error_text: str | None
    rate_limited: bool


@dataclass(frozen=True)
class ProbeResult:
    """Результат getMe-зонда, сохранён для обратной совместимости с модулями,
    которые различают «не дошли до Telegram» и «дошли, но ответ не ok»."""

    bot_alive: bool | None
    telegram_reachable: bool
    latency_ms: int
    http_status: int | None
    error_text: str | None
    rate_limited: bool
    retry_after_sec: int | None = None


def _parse_retry_after(resp: httpx.Response, data: dict | None) -> int:
    retry: int | None = None
    if data:
        params = data.get("parameters")
        if isinstance(params, dict) and params.get("retry_after") is not None:
            try:
                retry = int(params["retry_after"])
            except (TypeError, ValueError):
                retry = None
    if retry is None:
        header = resp.headers.get("Retry-After")
        if header:
            try:
                retry = int(header)
            except ValueError:
                retry = None
    if retry is None:
        retry = 60
    return max(1, min(retry, 600))


def _parse_json(resp: httpx.Response) -> dict | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


async def probe_getme_api(
    client: httpx.AsyncClient, token: str, timeout_sec: float
) -> ProbeResult:
    """Проверка доступности Telegram Bot API с VPS Botping.

    Используется с токеном admin-бота как глобальная проверка. Отдельно —
    в хендлере добавления нового бота для валидации токена.
    """
    url = f"https://api.telegram.org/bot{token}/getMe"
    t0 = time.perf_counter()
    try:
        resp = await client.get(url, timeout=timeout_sec)
    except httpx.TimeoutException:
        ms = int((time.perf_counter() - t0) * 1000)
        return ProbeResult(None, False, ms, None, "timeout", False)
    except httpx.RequestError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        err = e.__class__.__name__
        detail = str(e).strip()
        if detail:
            err = f"{err}: {detail[:120]}"
        return ProbeResult(None, False, ms, None, err, False)

    ms = int((time.perf_counter() - t0) * 1000)
    status = resp.status_code
    data = _parse_json(resp)

    if status >= 500 and status < 600:
        return ProbeResult(None, False, ms, status, f"http_{status}", False)

    if status == 429 or (data is not None and data.get("error_code") == 429):
        err = (data or {}).get("description") or "rate_limited"
        return ProbeResult(
            None,
            True,
            ms,
            status,
            str(err),
            True,
            retry_after_sec=_parse_retry_after(resp, data),
        )

    if data is None:
        return ProbeResult(None, False, ms, status, "invalid_json", False)

    if data.get("ok") is True:
        return ProbeResult(True, True, ms, status, None, False)

    err = str(data.get("description") or "telegram_error")
    return ProbeResult(False, True, ms, status, err, False)


async def check_getme(
    client: httpx.AsyncClient, token: str, timeout_sec: float
) -> CheckResult:
    """Sanity-check токена бота при добавлении в мониторинг."""
    r = await probe_getme_api(client, token, timeout_sec)
    ok = r.bot_alive is True and r.telegram_reachable
    return CheckResult(
        ok=ok,
        latency_ms=r.latency_ms,
        http_status=r.http_status,
        error_text=r.error_text,
        rate_limited=r.rate_limited,
    )
