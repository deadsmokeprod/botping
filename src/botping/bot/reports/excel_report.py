from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

THIN = Side(style="thin", color="CCCCCC")
HEADER_FONT = Font(bold=True, size=11)
HEADER_FILL = PatternFill("solid", fgColor="E8EEF7")
TITLE_FONT = Font(bold=True, size=13)
WRAP = Alignment(wrap_text=True, vertical="top")


def _style_header(ws, row: int, ncols: int) -> None:
    for col in range(1, ncols + 1):
        c = ws.cell(row=row, column=col)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        c.alignment = Alignment(vertical="center", wrap_text=True)


def _autosize(ws, max_width: int = 56) -> None:
    for col in ws.columns:
        cells = [c for c in col]
        if not cells:
            continue
        letter = get_column_letter(cells[0].column)
        length = max((len(str(c.value or "")) for c in cells), default=0)
        ws.column_dimensions[letter].width = min(max_width, max(10, length + 2))


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _format_minutes(total_sec: float) -> str:
    m = int(total_sec // 60)
    if m >= 60:
        h = m // 60
        rem = m % 60
        return f"{h} ч {rem} мин" if rem else f"{h} ч"
    s = int(total_sec) % 60
    if m == 0:
        return f"{s} с"
    return f"{m} мин {s} с" if s else f"{m} мин"


def _incident_duration_sec(
    inc: dict[str, Any], period_end: datetime, generated_at: datetime
) -> float:
    start = _parse_ts(str(inc["started_at"]))
    if start is None:
        return 0.0
    end_raw = inc.get("ended_at")
    if end_raw:
        end = _parse_ts(str(end_raw)) or period_end
    else:
        # открытый инцидент — считаем до момента формирования отчёта
        end = generated_at
    return max(0.0, (end - start).total_seconds())


def _bot_status_text(ok: bool, error: str) -> str:
    if ok:
        return "Живой"
    return "Недоступен"


def build_availability_report(
    *,
    period_start: datetime,
    period_end: datetime,
    generated_at: datetime,
    bots: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
    audit: list[dict[str, Any]],
    settings_rows: list[tuple[str, str]],
    checks_truncated: bool,
    incidents_truncated: bool,
    audit_truncated: bool,
    checks_total_in_db: int = 0,
    checks_db_min_ts: str | None = None,
    checks_db_max_ts: str | None = None,
    telegram_checks: list[dict[str, Any]] | None = None,
    telegram_incidents: list[dict[str, Any]] | None = None,
    telegram_checks_truncated: bool = False,
    telegram_incidents_truncated: bool = False,
    routers: list[dict[str, Any]] | None = None,
    router_target_checks: list[dict[str, Any]] | None = None,
    router_incidents: list[dict[str, Any]] | None = None,
    router_target_incidents: list[dict[str, Any]] | None = None,
    router_checks_truncated: bool = False,
    router_incidents_truncated: bool = False,
    router_target_incidents_truncated: bool = False,
    websites: list[dict[str, Any]] | None = None,
    website_module_checks: list[dict[str, Any]] | None = None,
    website_incidents: list[dict[str, Any]] | None = None,
    website_module_incidents: list[dict[str, Any]] | None = None,
    website_checks_truncated: bool = False,
    website_incidents_truncated: bool = False,
    website_module_incidents_truncated: bool = False,
) -> bytes:
    telegram_checks = telegram_checks or []
    routers = routers or []
    router_target_checks = router_target_checks or []
    router_incidents = router_incidents or []
    router_target_incidents = router_target_incidents or []
    telegram_incidents = telegram_incidents or []
    websites = websites or []
    website_module_checks = website_module_checks or []
    website_incidents = website_incidents or []
    website_module_incidents = website_module_incidents or []

    wb = Workbook()

    # =========================================================================
    # Сводка — человеко-читаемое резюме
    # =========================================================================
    ws0 = wb.active
    ws0.title = "Сводка"
    ws0.cell(row=1, column=1, value="Отчёт Botping — доступность ботов").font = TITLE_FONT
    ws0.append([])
    ws0.append(["Период с", period_start.strftime("%d.%m.%Y %H:%M:%S")])
    ws0.append(["Период по", period_end.strftime("%d.%m.%Y %H:%M:%S")])
    ws0.append(["Сформирован", generated_at.strftime("%d.%m.%Y %H:%M:%S")])
    ws0.append([])

    # Человеко-читаемое резюме
    total_downtime_per_bot: dict[int, float] = {}
    longest_inc: dict[str, Any] | None = None
    longest_dur = 0.0
    for inc in incidents:
        d = _incident_duration_sec(inc, period_end, generated_at)
        bid = int(inc["bot_id"])
        total_downtime_per_bot[bid] = total_downtime_per_bot.get(bid, 0.0) + d
        if d > longest_dur:
            longest_dur = d
            longest_inc = inc

    total_downtime = sum(total_downtime_per_bot.values())
    tg_downtime = sum(
        _incident_duration_sec(x, period_end, generated_at) for x in telegram_incidents
    )

    ws0.cell(row=ws0.max_row + 1, column=1, value="Итоги периода (коротко)").font = TITLE_FONT
    ws0.append([])
    ws0.append([
        "Суммарная недоступность всех ботов",
        _format_minutes(total_downtime) if total_downtime else "ноль — инцидентов не было",
    ])
    ws0.append([
        "Самый длинный инцидент",
        (
            f"{longest_inc['display_name']} (id={longest_inc['bot_id']}): "
            f"с {longest_inc['started_at']} по {longest_inc['ended_at'] or 'сейчас'}, "
            f"длительность {_format_minutes(longest_dur)}"
        )
        if longest_inc
        else "—",
    ])
    ws0.append([
        "Недоступность Telegram API (сумма)",
        _format_minutes(tg_downtime) if tg_downtime else "ноль — Telegram API был доступен всё время",
    ])

    if total_downtime_per_bot:
        ws0.append([])
        ws0.cell(row=ws0.max_row + 1, column=1, value="Недоступность по каждому боту").font = TITLE_FONT
        ws0.append([])
        name_by_id = {int(b["id"]): b["display_name"] for b in bots}
        for bid, dur in sorted(total_downtime_per_bot.items(), key=lambda x: -x[1]):
            ws0.append([
                f"{name_by_id.get(bid, '?')} (id={bid})",
                _format_minutes(dur),
            ])

    ws0.append([])
    ws0.cell(row=ws0.max_row + 1, column=1, value="Что на каждом листе").font = TITLE_FONT
    ws0.append([])
    ws0.append([
        "Лист «Проверки»",
        "каждая строка — одна оценка бота (раз в интервал проверки). "
        "Столбец «Статус» — «Живой» или «Недоступен» по свежести heartbeat.",
    ])
    ws0.append([
        "Лист «Инциденты»",
        "эпизоды, когда бот был недоступен несколько проверок подряд и был открыт инцидент. "
        "Есть колонка «Длительность».",
    ])
    ws0.append([
        "Лист «Telegram API»",
        "каждая строка — одна проверка самого Telegram API с VPS Botping (getMe к admin-боту).",
    ])
    ws0.append([
        "Лист «Инциденты Telegram API»",
        "когда с VPS не получалось достучаться до api.telegram.org.",
    ])
    ws0.append(["Лист «Боты»", "снимок списка мониторинга на момент отчёта, включая heartbeat URL и маску секрета."])
    ws0.append(["Лист «Аудит настроек»", "кто и когда менял параметры через Telegram."])
    ws0.append(["Лист «Настройки сейчас»", "снимок всех ключей из таблицы settings."])
    ws0.append(["Лист «Легенда»", "словарь терминов."])

    ws0.append([])
    ws0.append(["Строк проверок в периоде", f"{len(checks)}{' (обрезано)' if checks_truncated else ''}"])
    ws0.append(["Строк инцидентов", f"{len(incidents)}{' (обрезано)' if incidents_truncated else ''}"])
    ws0.append([
        "Строк проверок Telegram API",
        f"{len(telegram_checks)}{' (обрезано)' if telegram_checks_truncated else ''}",
    ])
    ws0.append([
        "Строк инцидентов Telegram API",
        f"{len(telegram_incidents)}{' (обрезано)' if telegram_incidents_truncated else ''}",
    ])
    ws0.append(["Строк аудита", f"{len(audit)}{' (обрезано)' if audit_truncated else ''}"])
    ws0.append(["Количество ботов (всего)", len(bots)])
    ws0.append([])
    ws0.append(["Проверок в базе всего", checks_total_in_db])
    ws0.append(["Самая ранняя метка в БД", checks_db_min_ts or "—"])
    ws0.append(["Самая поздняя метка в БД", checks_db_max_ts or "—"])

    ws0.column_dimensions["A"].width = 40
    ws0.column_dimensions["B"].width = 80

    # =========================================================================
    # Боты
    # =========================================================================
    wsb = wb.create_sheet("Боты")
    wsb.append([
        "ID",
        "Имя в мониторинге",
        "Включён",
        "Создан в БД (Москва)",
        "Последний heartbeat",
        "С IP",
        "Секрет (маска)",
        "Примечание",
    ])
    _style_header(wsb, 1, 8)
    for b in bots:
        sec = str(b.get("heartbeat_secret") or "")
        sec_mask = f"{sec[:4]}…{sec[-4:]}" if len(sec) > 8 else "***"
        wsb.append([
            b["id"],
            b["display_name"],
            "да" if b["enabled"] else "нет",
            b["created_at"],
            b.get("last_heartbeat_at") or "—",
            b.get("last_heartbeat_ip") or "—",
            sec_mask if sec else "—",
            "Токен и полный секрет в отчёт не выводятся",
        ])
    for row in wsb.iter_rows(min_row=2, max_row=wsb.max_row, min_col=1, max_col=8):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    _autosize(wsb)

    # =========================================================================
    # Проверки (per-bot)
    # =========================================================================
    wsc = wb.create_sheet("Проверки")
    wsc.append([
        "Время (Москва)",
        "Бот",
        "Статус",
        "Прошло с последнего пинга",
        "Примечание",
        "Тип проверки",
    ])
    _style_header(wsc, 1, 6)
    for r in checks:
        lat_ms = r.get("latency_ms")
        if lat_ms is None:
            age_text = "нет данных"
        else:
            age_text = _format_minutes(int(lat_ms) / 1000.0)
        status = _bot_status_text(bool(r["ok"]), r.get("error_text") or "")
        wsc.append([
            r["ts"],
            r["display_name"],
            status,
            age_text,
            r.get("error_text") or "",
            r.get("check_type") or "heartbeat",
        ])
    for row in wsc.iter_rows(min_row=2, max_row=wsc.max_row, min_col=1, max_col=6):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wsc.freeze_panes = "A2"
    _autosize(wsc)

    # =========================================================================
    # Инциденты ботов
    # =========================================================================
    wsi = wb.create_sheet("Инциденты")
    wsi.append([
        "ID инцидента",
        "Бот",
        "Начало (Москва)",
        "Конец (пусто = ещё открыт)",
        "Длительность",
        "Последняя ошибка",
        "Последний алерт",
    ])
    _style_header(wsi, 1, 7)
    for r in incidents:
        dur = _incident_duration_sec(r, period_end, generated_at)
        wsi.append([
            r["id"],
            r["display_name"],
            r["started_at"],
            r["ended_at"] or "",
            _format_minutes(dur),
            r["last_error"] or "",
            r.get("last_alert_at") or "",
        ])
    for row in wsi.iter_rows(min_row=2, max_row=wsi.max_row, min_col=1, max_col=7):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wsi.freeze_panes = "A2"
    _autosize(wsi)

    # =========================================================================
    # Telegram API
    # =========================================================================
    wst = wb.create_sheet("Telegram API")
    wst.append([
        "Время (Москва)",
        "Статус",
        "Задержка мс",
        "HTTP",
        "Ошибка",
        "Rate limit",
    ])
    _style_header(wst, 1, 6)
    for r in telegram_checks:
        wst.append([
            r["ts"],
            "Доступен" if r["ok"] else "Недоступен",
            r["latency_ms"] if r["latency_ms"] is not None else "",
            r["http_status"] if r["http_status"] is not None else "",
            r["error_text"] or "",
            "да" if r["rate_limited"] else "",
        ])
    for row in wst.iter_rows(min_row=2, max_row=wst.max_row, min_col=1, max_col=6):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wst.freeze_panes = "A2"
    _autosize(wst)

    # =========================================================================
    # Инциденты Telegram API
    # =========================================================================
    wsti = wb.create_sheet("Инциденты Telegram API")
    wsti.append([
        "ID",
        "Начало (Москва)",
        "Конец (пусто = ещё открыт)",
        "Длительность",
        "Последняя ошибка",
        "Последний алерт",
    ])
    _style_header(wsti, 1, 6)
    for r in telegram_incidents:
        dur = _incident_duration_sec(r, period_end, generated_at)
        wsti.append([
            r["id"],
            r["started_at"],
            r["ended_at"] or "",
            _format_minutes(dur),
            r["last_error"] or "",
            r.get("last_alert_at") or "",
        ])
    for row in wsti.iter_rows(min_row=2, max_row=wsti.max_row, min_col=1, max_col=6):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wsti.freeze_panes = "A2"
    _autosize(wsti)

    # =========================================================================
    # Аудит настроек
    # =========================================================================
    wsa = wb.create_sheet("Аудит настроек")
    wsa.append(["Время (Москва)", "ID админа Telegram", "Параметр", "Было", "Стало"])
    _style_header(wsa, 1, 5)
    for r in audit:
        wsa.append([r["ts"], r["admin_chat_id"], r["key"], r["old_value"] or "", r["new_value"] or ""])
    for row in wsa.iter_rows(min_row=2, max_row=wsa.max_row, min_col=1, max_col=5):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wsa.freeze_panes = "A2"
    _autosize(wsa)

    # =========================================================================
    # Настройки сейчас
    # =========================================================================
    wss = wb.create_sheet("Настройки сейчас")
    wss.append(["Ключ", "Значение в БД"])
    _style_header(wss, 1, 2)
    for k, v in settings_rows:
        wss.append([k, v])
    for row in wss.iter_rows(min_row=2, max_row=wss.max_row, min_col=1, max_col=2):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    _autosize(wss)

    # =========================================================================
    # Легенда
    # =========================================================================
    wsl = wb.create_sheet("Легенда")
    legend = [
        (
            "Как работает мониторинг",
            "Каждый ваш бот сам раз в ~30 секунд шлёт короткий HTTP-запрос на Botping "
            "(heartbeat). Botping на каждом тике смотрит, насколько свеж последний пинг: "
            "если он старше таймаута — бот считается недоступным.",
        ),
        ("Живой", "От бота пришёл хотя бы один heartbeat не позже, чем разрешено таймаутом."),
        (
            "Недоступен",
            "Бот не пингует Botping дольше таймаута. Обычные причины: процесс остановлен, "
            "у сервера бота нет интернета, сломан сниппет или неправильный секрет.",
        ),
        (
            "Прошло с последнего пинга",
            "Возраст самого свежего heartbeat на момент проверки. Для живого бота — "
            "обычно меньше таймаута.",
        ),
        (
            "Инцидент",
            "Период, когда бот подряд несколько тиков был «Недоступен» и превысил порог "
            "fail_threshold. Длительность считается от «Начало» до «Конец» (для открытых — "
            "до момента формирования отчёта).",
        ),
        (
            "Telegram API",
            "Отдельная проверка: раз в тик Botping делает getMe своим токеном админ-бота. "
            "Если не ответил — значит с VPS Botping проблема со связью с Telegram. "
            "Это НЕ то же самое, что «бот недоступен».",
        ),
        (
            "Тип проверки",
            "'heartbeat' — новый способ (пинг от самого бота). 'getupdates'/'getme' — исторические "
            "значения из старых версий, сохранены для совместимости.",
        ),
    ]
    wsl.append(["Термин", "Объяснение"])
    _style_header(wsl, 1, 2)
    for title, text in legend:
        wsl.append([title, text])
    wsl.column_dimensions["A"].width = 28
    wsl.column_dimensions["B"].width = 100
    for row in wsl.iter_rows(min_row=2, max_row=wsl.max_row, min_col=1, max_col=2):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP

    if routers:
        wsr = wb.create_sheet("Роутеры")
        wsr.append([
            "ID",
            "Имя",
            "Включён",
            "Последний heartbeat",
            "С IP",
        ])
        _style_header(wsr, 1, 5)
        for r in routers:
            wsr.append([
                r["id"],
                r["display_name"],
                "да" if r["enabled"] else "нет",
                r.get("last_heartbeat_at") or "—",
                r.get("last_heartbeat_ip") or "—",
            ])
        _autosize(wsr)

    if router_target_checks:
        wslc = wb.create_sheet("Проверки LAN")
        wslc.append([
            "Время",
            "Роутер",
            "Цель",
            "Адрес",
            "Статус",
            "мс",
            "Ошибка",
            "Тип",
        ])
        _style_header(wslc, 1, 8)
        for r in router_target_checks:
            wslc.append([
                r["ts"],
                r["router_name"],
                r["target_name"],
                r["address"],
                "Живой" if r["ok"] else "Недоступен",
                r["latency_ms"] if r["latency_ms"] is not None else "",
                r["error_text"] or "",
                r.get("check_type") or "lan_push",
            ])
        wslc.freeze_panes = "A2"
        _autosize(wslc)

    if router_incidents or router_target_incidents:
        wsri = wb.create_sheet("Инциденты сайтов")
        wsri.append([
            "Тип",
            "Объект",
            "Начало",
            "Конец",
            "Длительность",
            "Ошибка",
        ])
        _style_header(wsri, 1, 6)
        for r in router_incidents:
            dur = _incident_duration_sec(r, period_end, generated_at)
            wsri.append([
                "роутер",
                r["display_name"],
                r["started_at"],
                r["ended_at"] or "",
                _format_minutes(dur),
                r["last_error"] or "",
            ])
        for r in router_target_incidents:
            dur = _incident_duration_sec(r, period_end, generated_at)
            label = f"{r['router_name']} / {r['target_name']} ({r['address']})"
            wsri.append([
                "LAN",
                label,
                r["started_at"],
                r["ended_at"] or "",
                _format_minutes(dur),
                r["last_error"] or "",
            ])
        wsri.freeze_panes = "A2"
        _autosize(wsri)

    if websites:
        wsw = wb.create_sheet("Сайты")
        wsw.append([
            "ID",
            "Имя",
            "Домен",
            "Включён",
            "IP",
            "Последний heartbeat",
            "Агент IP",
        ])
        _style_header(wsw, 1, 7)
        for w in websites:
            wsw.append([
                w["id"],
                w["display_name"],
                w["host"],
                "да" if w["enabled"] else "нет",
                w.get("last_resolved_ip") or "—",
                w.get("last_heartbeat_at") or "—",
                w.get("last_heartbeat_ip") or "—",
            ])
        _autosize(wsw)

    if website_module_checks:
        wsmc = wb.create_sheet("Проверки модулей")
        wsmc.append([
            "Время",
            "Сайт",
            "Модуль",
            "Домен",
            "Статус",
            "мс",
            "Ошибка",
            "Тип",
        ])
        _style_header(wsmc, 1, 8)
        for r in website_module_checks:
            wsmc.append([
                r["ts"],
                r["website_name"],
                r["module_name"],
                r["host"],
                "Живой" if r["ok"] else "Недоступен",
                r["latency_ms"] if r["latency_ms"] is not None else "",
                r["error_text"] or "",
                r.get("check_type") or "module_push",
            ])
        wsmc.freeze_panes = "A2"
        _autosize(wsmc)

    if website_incidents or website_module_incidents:
        wswi = wb.create_sheet("Инциденты веб")
        wswi.append([
            "Тип",
            "Объект",
            "Начало",
            "Конец",
            "Длительность",
            "Ошибка",
        ])
        _style_header(wswi, 1, 6)
        for r in website_incidents:
            dur = _incident_duration_sec(r, period_end, generated_at)
            label = f"{r['display_name']} ({r['host']})"
            wswi.append([
                "сайт",
                label,
                r["started_at"],
                r["ended_at"] or "",
                _format_minutes(dur),
                r["last_error"] or "",
            ])
        for r in website_module_incidents:
            dur = _incident_duration_sec(r, period_end, generated_at)
            label = f"{r['website_name']} / {r['module_name']} ({r['host']})"
            wswi.append([
                "модуль",
                label,
                r["started_at"],
                r["ended_at"] or "",
                _format_minutes(dur),
                r["last_error"] or "",
            ])
        wswi.freeze_panes = "A2"
        _autosize(wswi)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
