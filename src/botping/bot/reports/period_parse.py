from __future__ import annotations

import re
import unicodedata
from datetime import datetime

# Явные разделители «от — до» (кириллическое «по», дефисы, запятая, to)
_SEPARATORS_ORDERED = [
    " по ",  # основной вариант из Telegram (UTF-8)
    " PO ",  # на случай латиницы
    " to ",
    " To ",
    " — ",  # длинное тире
    " – ",  # среднее тире
    " - ",
    ",",  # частый ввод без слова «по»
]


def _parse_one_date(token: str) -> datetime:
    t = token.strip()
    if not t:
        raise ValueError("пустая дата")
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(t, fmt).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    if re.fullmatch(r"\d{8}", t):
        return datetime.strptime(t, "%d%m%Y").replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if re.fullmatch(r"\d{6}", t):
        return datetime.strptime(t, "%d%m%y").replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    raise ValueError(f"не удалось разобрать дату: {token!r}")


def _normalize_line(line: str) -> str:
    s = unicodedata.normalize("NFKC", line.strip())
    s = re.sub(r"[\u200b-\u200d\ufeff\u2060]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_into_two(s: str) -> tuple[str, str]:
    """Разбивает строку на две даты без парсинга самих дат."""
    # Только цифры: 15042025 по 20042025 или через запятую без пробелов
    compact = re.fullmatch(
        r"(\d{6}|\d{8})\s*(?:по|PO|po|to)\s*(\d{6}|\d{8})",
        s,
        flags=re.IGNORECASE,
    )
    if compact:
        return compact.group(1).strip(), compact.group(2).strip()
    compact_comma = re.fullmatch(r"(\d{6}|\d{8}),(\d{6}|\d{8})", s)
    if compact_comma:
        return compact_comma.group(1).strip(), compact_comma.group(2).strip()

    for sep in _SEPARATORS_ORDERED:
        if sep in s:
            left, right = s.split(sep, 1)
            return left.strip(), right.strip()

    raise ValueError(
        "Нужны две даты: «от» и «до». Примеры:\n"
        "• 15.04.2026 по 20.04.2026\n"
        "• 15042025 по 20042025\n"
        "• 15042025,20042025\n"
        "• 150425 по 200425"
    )


def parse_period_line(line: str) -> tuple[datetime, datetime]:
    """
    Период «от — до» по календарным датам (интерпретация как сутки по Москве, UTC+3).

    Примеры:
    - 15.04.2026 по 20.04.2026
    - 15042026 по 20042026   (ДДММГГГГ)
    - 150426 по 200426       (ДДММГГ, год 20ГГ)
    """
    raw = _normalize_line(line)
    left_s, right_s = _split_into_two(raw)
    start = _parse_one_date(left_s)
    end = _parse_one_date(right_s)
    if end < start:
        start, end = end, start
    end_inclusive = end.replace(hour=23, minute=59, second=59)
    return start, end_inclusive
