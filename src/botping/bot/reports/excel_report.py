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
WRAP = Alignment(wrap_text=True, vertical="top")


def _style_header(ws, row: int, ncols: int) -> None:
    for col in range(1, ncols + 1):
        c = ws.cell(row=row, column=col)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        c.alignment = Alignment(vertical="center", wrap_text=True)


def _autosize(ws, max_width: int = 48) -> None:
    for col in ws.columns:
        cells = [c for c in col]
        if not cells:
            continue
        letter = get_column_letter(cells[0].column)
        length = max((len(str(c.value or "")) for c in cells), default=0)
        ws.column_dimensions[letter].width = min(max_width, max(10, length + 2))


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
) -> bytes:
    wb = Workbook()
    # --- Сводка ---
    ws0 = wb.active
    ws0.title = "Сводка"
    ws0.append(["Отчёт Botping — доступность ботов"])
    ws0.append([])
    ws0.append(["Период с", period_start.strftime("%d.%m.%Y %H:%M:%S")])
    ws0.append(["Период по", period_end.strftime("%d.%m.%Y %H:%M:%S")])
    ws0.append(["Сформирован", generated_at.strftime("%d.%m.%Y %H:%M:%S")])
    ws0.append([])
    ws0.append(["Проверок в базе всего (за всё время)", checks_total_in_db])
    ws0.append(["Самая ранняя метка времени в таблице checks", checks_db_min_ts or "—"])
    ws0.append(["Самая поздняя метка времени в таблице checks", checks_db_max_ts or "—"])
    ws0.append([])
    ws0.append(
        [
            "Почему лист «Проверки» может быть пустым",
            "В лист попадают только строки за выбранный период (сравнение по тем же строкам времени, что в БД — время Москвы). "
            "Если в интервале выше нет совпадений — период отчёта не пересекается с тем, когда реально шёл опрос "
            "(сервис был выключен, боты выключены, выбраны другие даты/год). "
            "Строки появляются только пока работает процесс мониторинга и есть включённые боты.",
        ]
    )
    ws0.append([])
    ws0.append(["Лист «Проверки»", "каждая строка — один вызов getMe: успех/ошибка, задержка, код HTTP, текст ошибки, rate limit"])
    ws0.append(["Лист «Инциденты»", "эпизоды недоступности, пересекающие выбранный период"])
    ws0.append(["Лист «Боты»", "снимок списка мониторинга на момент отчёта"])
    ws0.append(["Лист «Аудит настроек»", "кто и когда менял параметры (если были изменения в периоде)"])
    ws0.append([])
    ws0.append(["Количество ботов (всего)", len(bots)])
    ws0.append(
        [
            "Строк проверок в периоде",
            f"{len(checks)}{' (обрезано по лимиту)' if checks_truncated else ''}",
        ]
    )
    ws0.append(
        [
            "Строк инцидентов",
            f"{len(incidents)}{' (обрезано)' if incidents_truncated else ''}",
        ]
    )
    ws0.append(
        ["Строк аудита", f"{len(audit)}{' (обрезано)' if audit_truncated else ''}"]
    )
    ws0.column_dimensions["A"].width = 28
    ws0.column_dimensions["B"].width = 80

    # --- Боты ---
    wsb = wb.create_sheet("Боты")
    wsb.append(
        [
            "ID",
            "Имя в мониторинге",
            "Включён (1 да / 0 нет)",
            "Создан в БД (Москва)",
            "Примечание",
        ]
    )
    _style_header(wsb, 1, 5)
    for b in bots:
        wsb.append(
            [
                b["id"],
                b["display_name"],
                1 if b["enabled"] else 0,
                b["created_at"],
                "Токен в отчёт не выводится из соображений безопасности",
            ]
        )
    for row in wsb.iter_rows(min_row=2, max_row=wsb.max_row, min_col=1, max_col=5):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    _autosize(wsb)

    # --- Проверки ---
    wsc = wb.create_sheet("Проверки")
    wsc.append(
        [
            "ID проверки",
            "ID бота",
            "Имя бота",
            "Время (Москва)",
            "Успех (1 да / 0 нет)",
            "Задержка мс",
            "HTTP статус",
            "Текст ошибки",
            "Rate limit (1 да)",
        ]
    )
    _style_header(wsc, 1, 9)
    for r in checks:
        wsc.append(
            [
                r["id"],
                r["bot_id"],
                r["display_name"],
                r["ts"],
                1 if r["ok"] else 0,
                r["latency_ms"] if r["latency_ms"] is not None else "",
                r["http_status"] if r["http_status"] is not None else "",
                r["error_text"] or "",
                1 if r["rate_limited"] else 0,
            ]
        )
    for row in wsc.iter_rows(min_row=2, max_row=wsc.max_row, min_col=1, max_col=9):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wsc.freeze_panes = "A2"
    _autosize(wsc, max_width=56)

    # --- Инциденты ---
    wsi = wb.create_sheet("Инциденты")
    wsi.append(
        [
            "ID инцидента",
            "ID бота",
            "Имя бота",
            "Начало (Москва)",
            "Окончание (Москва, пусто = ещё открыт)",
            "Последняя ошибка в проверке",
            "Последний алерт (Москва)",
        ]
    )
    _style_header(wsi, 1, 7)
    for r in incidents:
        wsi.append(
            [
                r["id"],
                r["bot_id"],
                r["display_name"],
                r["started_at"],
                r["ended_at"] or "",
                r["last_error"] or "",
                r.get("last_alert_at") or "",
            ]
        )
    for row in wsi.iter_rows(min_row=2, max_row=wsi.max_row, min_col=1, max_col=7):
        for c in row:
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            c.alignment = WRAP
    wsi.freeze_panes = "A2"
    _autosize(wsi)

    # --- Аудит настроек ---
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

    # --- Текущие настройки ---
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

    # --- Легенда ---
    wsl = wb.create_sheet("Легенда")
    legend = [
        ("Проверки", "Каждая запись — результат одного getMe к Bot API для токена бота."),
        ("Успех", "1 = ответ ok, бот считается доступным в этот момент."),
        ("Задержка мс", "Время ответа getMe (для анализа деградации сети)."),
        ("HTTP статус", "Код ответа HTTP; может быть пусто при таймауте/сетевой ошибке."),
        ("Текст ошибки", "Краткое описание от Telegram или timeout/invalid_json."),
        ("Rate limit", "1 если Telegram вернул 429 — в логике мониторинга не увеличивает счётчик падений."),
        ("Инциденты", "Период, когда бот признан недоступным (порог подряд неудач), до восстановления."),
        ("Аудит", "Изменения параметров из Telegram (ваш user id в колонке админа)."),
    ]
    for title, text in legend:
        wsl.append([title, text])
    wsl.column_dimensions["A"].width = 22
    wsl.column_dimensions["B"].width = 90
    for row in wsl.iter_rows(min_row=1, max_row=wsl.max_row, min_col=1, max_col=2):
        for c in row:
            c.alignment = WRAP

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
