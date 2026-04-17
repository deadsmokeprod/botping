from __future__ import annotations

import time
from dataclasses import dataclass
import httpx


@dataclass(frozen=True)
class CheckResult:
    """Совместимый результат для getMe-проверок (sanity-check токена и Telegram API)."""

    ok: bool
    latency_ms: int
    http_status: int | None
    error_text: str | None
    rate_limited: bool


@dataclass(frozen=True)
class ProbeResult:
    """Результат основного per-bot зонда getUpdates.

    bot_alive: True  — получили 409 Conflict (кто-то уже поллит => бот жив).
    bot_alive: False — получили 200 ok=true (никто не поллит => бот не работает)
                       либо 401/404 (токен невалиден).
    bot_alive: None  — не смогли достучаться до Telegram (сеть/таймаут/5xx),
                       состояние бота неизвестно.
    telegram_reachable — удалось ли получить осмысленный HTTP-ответ от Telegram API.
    """

    bot_alive: bool | None
    telegram_reachable: bool
    latency_ms: int
    http_status: int | None
    error_text: str | None
    rate_limited: bool


def _parse_json(resp: httpx.Response) -> dict | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


async def probe_getupdates(
    client: httpx.AsyncClient, token: str, timeout_sec: float
) -> ProbeResult:
    """Основная проверка живости бота.

    Дергаем getUpdates с timeout=0, offset=-1, limit=1. offset=-1 не двигает
    внутренний курсор update_id, просто запрашивает последний апдейт.

    - 409 Conflict (HTTP-статус или error_code в теле) — значит другой инстанс
      прямо сейчас ведёт long-poll: бот жив.
    - 200 ok=true — никто не поллит, бот «молчит»: процесс не запущен.
    - 401/404 — токен невалиден, формально бот не работает.
    - 429 — rate limit (как и раньше — не засчитываем как падение).
    - Таймаут/сетевая ошибка/5xx — Telegram API нам самим недоступен.
    """
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    payload = {"timeout": 0, "limit": 1, "offset": -1}
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, timeout=timeout_sec)
    except httpx.TimeoutException:
        ms = int((time.perf_counter() - t0) * 1000)
        return ProbeResult(None, False, ms, None, "timeout", False)
    except httpx.RequestError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return ProbeResult(None, False, ms, None, str(e.__class__.__name__), False)

    ms = int((time.perf_counter() - t0) * 1000)
    status = resp.status_code
    data = _parse_json(resp)

    if status >= 500 and status < 600:
        return ProbeResult(None, False, ms, status, f"http_{status}", False)

    if status == 429 or (data is not None and data.get("error_code") == 429):
        err = (data or {}).get("description") or "rate_limited"
        return ProbeResult(None, True, ms, status, str(err), True)

    if status == 409 or (data is not None and data.get("error_code") == 409):
        return ProbeResult(True, True, ms, status, None, False)

    if status == 200 and data is not None and data.get("ok") is True:
        return ProbeResult(False, True, ms, status, None, False)

    if data is not None and data.get("ok") is False:
        err = str(data.get("description") or "telegram_error")
        return ProbeResult(False, True, ms, status, err, False)

    return ProbeResult(None, False, ms, status, "invalid_response", False)


async def probe_getme_api(
    client: httpx.AsyncClient, token: str, timeout_sec: float
) -> ProbeResult:
    """Вторичная проверка: доступен ли сам Telegram Bot API.

    Формально это тот же getMe, что и раньше, но теперь его результат
    относится не к конкретному боту, а к глобальному статусу API.
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
        return ProbeResult(None, False, ms, None, str(e.__class__.__name__), False)

    ms = int((time.perf_counter() - t0) * 1000)
    status = resp.status_code
    data = _parse_json(resp)

    if status >= 500 and status < 600:
        return ProbeResult(None, False, ms, status, f"http_{status}", False)

    if status == 429 or (data is not None and data.get("error_code") == 429):
        err = (data or {}).get("description") or "rate_limited"
        return ProbeResult(None, True, ms, status, str(err), True)

    if data is None:
        return ProbeResult(None, False, ms, status, "invalid_json", False)

    if data.get("ok") is True:
        return ProbeResult(True, True, ms, status, None, False)

    err = str(data.get("description") or "telegram_error")
    return ProbeResult(False, True, ms, status, err, False)


async def check_getme(
    client: httpx.AsyncClient, token: str, timeout_sec: float
) -> CheckResult:
    """Совместимая обёртка над probe_getme_api для sanity-check токена
    при добавлении бота через /settings."""
    r = await probe_getme_api(client, token, timeout_sec)
    ok = r.bot_alive is True and r.telegram_reachable
    return CheckResult(
        ok=ok,
        latency_ms=r.latency_ms,
        http_status=r.http_status,
        error_text=r.error_text,
        rate_limited=r.rate_limited,
    )
