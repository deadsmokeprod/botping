from __future__ import annotations

import json
from botping.db import queries
from botping.db.pool import Database

# Эталон = значения по умолчанию из БД (совпадают с queries.DEFAULT_SETTINGS)
META: dict[str, dict[str, str]] = {
    "check_interval_sec": {
        "title": "Интервал оценки (секунды)",
        "body": (
            "Как часто Botping смотрит, пришёл ли свежий heartbeat от каждого бота, "
            "и открывает/закрывает инциденты. Проверка getMe к Telegram API идёт отдельно "
            "(см. «Интервал getMe»). Не путать с интервалом самого пинга — "
            "его задаёт ваш бот внутри сниппета (по умолчанию 30 с)."
        ),
        "etalon": "60",
        "etalon_hint": "1 раз в минуту — разумный баланс для большинства случаев.",
        "input": "Целое число секунд, не меньше 5 (ограничение в коде). Пример: 60",
    },
    "request_timeout_sec": {
        "title": "Таймаут одного запроса к Telegram (секунды)",
        "body": (
            "Сколько максимум ждать ответ API на один запрос (и getUpdates-зонд, и getMe). "
            "Если Telegram не ответил за это время — проверка считается неуспешной (таймаут). "
            "Полезно при «подвисающей» сети."
        ),
        "etalon": "15",
        "etalon_hint": "15 с обычно достаточно; на очень медленных каналах можно 20–30.",
        "input": "Целое число секунд, не меньше 1. Пример: 15",
    },
    "fail_threshold": {
        "title": "Порог пропусков подряд",
        "body": (
            "Сколько тиков подряд объект должен быть «плохим», чтобы открыть инцидент "
            "(бот, роутер, LAN, сайт, модуль). Сообщение в чат может уйти позже — "
            "см. «Задержка алерта down». Для глобальной проверки Telegram API — "
            "отдельный параметр «Порог сбоев Telegram»."
        ),
        "etalon": "2",
        "etalon_hint": "Двух пропусков подряд обычно достаточно.",
        "input": "Целое число, не меньше 1. Пример: 2",
    },
    "recover_threshold": {
        "title": "Порог восстановления подряд",
        "body": (
            "Сколько тиков подряд объект должен быть «хорошим», чтобы закрыть инцидент "
            "и отправить «Восстановлено». Защита от дребезга при флапе. "
            "Аналог «Порога восстановления Telegram» для ботов, сайтов и роутеров."
        ),
        "etalon": "2",
        "etalon_hint": "2 успешных тика подряд — разумный минимум.",
        "input": "Целое число, не меньше 1. Пример: 2",
    },
    "down_alert_sec": {
        "title": "Задержка алерта down (секунды)",
        "body": (
            "Сколько секунд с начала устойчивого сбоя ждать перед первым сообщением "
            "«Недоступен» в Telegram. Краткие обрывы — без алерта. "
            "0 — алерт сразу после порога пропусков. "
            "Аналог «Задержки алерта Telegram» для каждого устройства."
        ),
        "etalon": "0",
        "etalon_hint": "Для нестабильных модулей на сайте попробуйте 300–600.",
        "input": "Целое число секунд, 0 или больше. Пример: 600",
    },
    "repeat_alert_interval_sec": {
        "title": "Повтор алерта, пока бот down (секунды)",
        "body": (
            "Пока инцидент открыт и проверки всё ещё падают, повторное сообщение в чат не чаще, чем раз в этот интервал. "
            "Действует для инцидентов ботов, роутеров, сайтов и «Telegram API недоступен». "
            "Защита от спама одним и тем же алертом."
        ),
        "etalon": "3600",
        "etalon_hint": "3600 = раз в час напоминание, что бот всё ещё недоступен.",
        "input": "Целое число секунд, не меньше 60. Пример: 3600",
    },
    "slow_ms": {
        "title": "Порог «медленного» ответа (миллисекунды)",
        "body": (
            "Если объект «жив» (ok), но задержка больше порога — статус «медленно» и "
            "отдельный алерт «Медленный ответ» (те же пороги fail/recover/down delay). "
            "0 — функция выключена."
        ),
        "etalon": "0",
        "etalon_hint": "0 = не отслеживать скорость. Если нужно — попробуйте 3000–8000 мс.",
        "input": "Целое число миллисекунд. Пример: 0 или 5000",
    },
    "heartbeat_timeout_sec": {
        "title": "Таймаут heartbeat (секунды)",
        "body": (
            "Сколько секунд Botping готов ждать без пинга от бота. Если последний heartbeat "
            "старше этого значения — бот считается недоступным. "
            "Рекомендация: ставить ~3×(интервал пинга в сниппете). По умолчанию сниппет пингует "
            "раз в 30 секунд, поэтому 90–120 секунд здесь — разумный эталон. "
            "Слишком маленькое значение даст ложные тревоги при сетевых всплесках; "
            "слишком большое — поздно узнаете о реальном падении."
        ),
        "etalon": "120",
        "etalon_hint": "120 с = 4 пропуска подряд при интервале пинга 30 с — безопасный запас.",
        "input": "Целое число секунд, не меньше 30. Пример: 120",
    },
    "heartbeat_unauth_rate_per_min": {
        "title": "Лимит чужих запросов с одного IP (в минуту)",
        "body": (
            "Сколько запросов в минуту с одного IP разрешено, если запрос НЕ на /heartbeat "
            "или секрет неверный/отсутствует. Превышение — мгновенный 404. "
            "Чем меньше значение, тем быстрее сканеры получают отказ, но тем агрессивнее "
            "на случайные ошибки. "
            "ВАЖНО: IP, с которого прошёл успешный heartbeat за последний час, не попадает в "
            "авто-бан — только rate-limit. Так один сломанный бот не утащит в бан остальных "
            "на том же NAT."
        ),
        "etalon": "10",
        "etalon_hint": "10/мин с одного IP — достаточно для нормальных ошибок и быстро режет сканеров.",
        "input": "Целое число, не меньше 1. Пример: 10",
    },
    "heartbeat_ban_fails_threshold": {
        "title": "Порог неудач до автобана IP",
        "body": (
            "Сколько подряд неудачных попыток обратиться на /heartbeat (неверный или отсутствующий "
            "секрет) с одного IP нужно, чтобы добавить его во временный бан. "
            "Пока IP в бане — любые запросы от него сразу режутся на 404 без обращения к БД.\n\n"
            "Ваши боты с валидным секретом защищены: их IP не банится (см. «Лимит чужих запросов»)."
        ),
        "etalon": "6",
        "etalon_hint": "6 подряд неудач — нормальный компромисс против перебора.",
        "input": "Целое число, не меньше 1. Пример: 6",
    },
    "heartbeat_ban_duration_min": {
        "title": "Длительность автобана IP (минуты)",
        "body": (
            "На сколько минут IP попадает во временный бан после превышения порога неудач. "
            "В течение этого времени все запросы от него (включая правильные) отдают 404."
        ),
        "etalon": "15",
        "etalon_hint": "15 минут — достаточно, чтобы сканер устал и ушёл; не слишком долго для случайного ошибшегося.",
        "input": "Целое число минут, от 1 до 1440. Пример: 15",
    },
    "telegram_api_recover_threshold": {
        "title": "Порог восстановления Telegram API",
        "body": (
            "Сколько успешных getMe подряд нужно, чтобы закрыть инцидент Telegram API "
            "и отправить «Восстановлено» (только если ранее был алерт о падении)."
        ),
        "etalon": "2",
        "etalon_hint": "2 — как у общего recover_threshold.",
        "input": "Целое число, не меньше 1. Пример: 2",
    },
    "telegram_api_fail_threshold": {
        "title": "Порог сбоев Telegram API подряд",
        "body": (
            "Сколько неудачных getMe подряд нужно, чтобы завести инцидент в БД. "
            "Сообщение в чат уйдёт позже — см. «Задержка алерта Telegram». "
            "Большее значение отсекает краткие обрывы."
        ),
        "etalon": "3",
        "etalon_hint": "3 сбоя при интервале getMe 5 мин ≈ 15+ минут нестабильности.",
        "input": "Целое число, не меньше 1. Пример: 3",
    },
    "telegram_api_down_alert_sec": {
        "title": "Задержка алерта Telegram API (секунды)",
        "body": (
            "Сколько секунд с первого сбоя getMe ждать, прежде чем прислать "
            "«Telegram API недоступен». Если за это время связь восстановилась — "
            "в чат ничего не придёт (краткий обрыв). «Восстановлено» приходит только "
            "если раньше был алерт о падении. 0 — алерт сразу после порога сбоев."
        ),
        "etalon": "600",
        "etalon_hint": "600 с (10 мин) — меньше дребезга при флапе WAN/LTE.",
        "input": "Целое число секунд, 0 или больше. Пример: 600",
    },
    "telegram_api_check_interval_sec": {
        "title": "Интервал getMe к Telegram API (секунды)",
        "body": (
            "Как часто Botping делает getMe к admin-боту для проверки доступности "
            "api.telegram.org с VPS. Не чаще основного «Интервала оценки» — отдельная "
            "настройка, чтобы не слать лишние запросы (Telegram не любит частые зонды). "
            "После сетевой ошибки или 429 зонд дополнительно ждёт паузу (backoff), "
            "не долбит API при обрыве канала."
        ),
        "etalon": "300",
        "etalon_hint": "300 с (5 мин) — достаточно для мониторинга и щадяще к API.",
        "input": "Целое число секунд, не меньше 60. Пример: 300",
    },
    "telegram_api_probe_enabled": {
        "title": "Проверка Telegram API (0/1)",
        "body": (
            "Вторичная проверка: периодический getMe к токену admin-бота (интервал — "
            "«Интервал getMe»). "
            "Её результат НЕ влияет на per-bot инциденты — он показывает, доступен ли сам "
            "Telegram API с сервера Botping. Алерт только при устойчивом сбое "
            "(порог сбоев + задержка алерта); краткие обрывы — без сообщений в чат. "
            "Если выключить (0) — в /status будет строка "
            "«проверка отключена», и отдельного алерта о недоступности Telegram не будет."
        ),
        "etalon": "1",
        "etalon_hint": "По умолчанию включено (1). Выключайте только при необходимости.",
        "input": "0 (выкл) или 1 (вкл). Пример: 1",
    },
    "daily_excel_report_enabled": {
        "title": "Ежедневный Excel в 00:00 (Москва)",
        "body": (
            "Если включено (1), каждый день в полночь по Москве бот отправляет в чат(ы) админов тот же Excel-отчёт, "
            "что и по команде /report, за предыдущие календарные сутки. Если процесс был выключен несколько дней — "
            "при следующем запуске могут уйти несколько файлов подряд (по одному за каждый пропущенный день). "
            "0 — функция выключена."
        ),
        "etalon": "0",
        "etalon_hint": "По умолчанию выключено; включите 1, когда нужна автоматическая рассылка.",
        "input": "0 (выкл) или 1 (вкл). Пример: 0",
    },
    "quiet_hours": {
        "title": "Тихие часы (JSON)",
        "body": (
            "В указанный интервал суток по заданной таймзоне не отправляются новые и повторные алерты о недоступности "
            "(события всё равно пишутся в БД). Сообщение о восстановлении после сбоя уходит всегда. "
            "Переключение интернета WAN/LTE с роутера уходит в Telegram всегда, даже ночью. "
            "Пустой объект {} — тихих часов нет."
        ),
        "etalon": '{"start":"22:00","end":"07:00","tz":"Europe/Moscow"}',
        "etalon_hint": "Ночь 22:00–07:00 по Москве — пример; подставьте свой tz из IANA.",
        "input": (
            "Одной строкой JSON. Пример ночи: "
            '{"start":"22:00","end":"07:00","tz":"Europe/Moscow"}. '
            "Выключить: {}"
        ),
    },
    "disk_usage_threshold_pct": {
        "title": "Порог очистки диска (%)",
        "body": (
            "Когда занятость диска (где лежит БД) достигает этого порога — бот отправляет алерт в Telegram "
            "и начинает удалять самые старые проверки (принцип видеонаблюдения: новые данные важнее старых). "
            "После очистки выполняется VACUUM для реального освобождения места. "
            "Гистерезис: очистка идёт до (порог − 10)%, чтобы не дёргаться каждые 5 минут."
        ),
        "etalon": "80",
        "etalon_hint": "80% — оставляет 20% запас для ОС и других сервисов (VPN и т.д.).",
        "input": "Целое число от 50 до 95. Пример: 80",
    },
    "disk_check_interval_sec": {
        "title": "Интервал проверки диска (секунды)",
        "body": (
            "Как часто фоновый процесс проверяет заполненность диска. "
            "Слишком часто — лишняя нагрузка, слишком редко — можно не успеть среагировать."
        ),
        "etalon": "300",
        "etalon_hint": "300 с (5 минут) — разумный баланс.",
        "input": "Целое число секунд, не меньше 60. Пример: 300",
    },
}

SETTINGS_GROUPS: dict[str, dict[str, str]] = {
    "monitor": {
        "title": "Мониторинг и алерты",
        "emoji": "📡",
    },
    "notify": {
        "title": "Уведомления",
        "emoji": "🔔",
    },
    "telegram": {
        "title": "Telegram API",
        "emoji": "☁️",
    },
    "heartbeat_srv": {
        "title": "Heartbeat-сервер",
        "emoji": "💓",
    },
    "disk": {
        "title": "Диск и сервис",
        "emoji": "💾",
    },
}

SETTINGS_GROUP_KEYS: dict[str, list[str]] = {
    "monitor": [
        "check_interval_sec",
        "heartbeat_timeout_sec",
        "fail_threshold",
        "recover_threshold",
        "down_alert_sec",
        "repeat_alert_interval_sec",
        "slow_ms",
        "request_timeout_sec",
    ],
    "notify": ["quiet_hours", "daily_excel_report_enabled"],
    "telegram": [
        "telegram_api_probe_enabled",
        "telegram_api_check_interval_sec",
        "telegram_api_fail_threshold",
        "telegram_api_recover_threshold",
        "telegram_api_down_alert_sec",
    ],
    "heartbeat_srv": [
        "heartbeat_unauth_rate_per_min",
        "heartbeat_ban_fails_threshold",
        "heartbeat_ban_duration_min",
    ],
    "disk": ["disk_usage_threshold_pct", "disk_check_interval_sec"],
}

ENTITY_SETTINGS_GROUPS: dict[str, list[str]] = {
    "monitor": [
        "heartbeat_timeout_sec",
        "fail_threshold",
        "recover_threshold",
        "down_alert_sec",
        "repeat_alert_interval_sec",
        "slow_ms",
    ],
    "notify": ["quiet_hours"],
}

ENTITY_KIND_LABELS: dict[str, str] = {
    "bot": "бот",
    "router": "роутер",
    "target": "устройство LAN",
    "website": "сайт",
    "module": "модуль",
}


def setting_button_label(key: str) -> str:
    m = META.get(key, {})
    title = m.get("title", key)
    short = title.split("(")[0].strip()[:28]
    return short or key


async def _raw_value(db: Database, key: str) -> str:
    row = await queries.get_setting(db, key)
    if row is not None:
        return str(row)
    return queries.DEFAULT_SETTINGS.get(key, "")


def _pretty_quiet(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return "(пусто)"
    try:
        obj = json.loads(raw)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return raw[:200]


async def format_key_change_prompt(db: Database, key: str) -> str:
    m = META[key]
    cur = await _raw_value(db, key)
    et = queries.DEFAULT_SETTINGS.get(key, "")
    if key == "quiet_hours":
        cur_disp = _pretty_quiet(cur)
        et_disp = _pretty_quiet(et)
    else:
        cur_disp = cur
        et_disp = et
    parts = [
        f"Изменение: {m['title']}",
        "",
        m["body"],
        "",
        f"Эталон (рекомендуется): {et_disp}",
        f"Сейчас в базе: {cur_disp}",
        "",
        "Что ввести:",
        m["input"],
        "",
        "Отправьте следующим сообщением новое значение.",
    ]
    return "\n".join(parts)


async def format_entity_key_change_prompt(
    db: Database,
    kind: str,
    entity_id: int,
    key: str,
) -> str:
    global_parsed = await queries.load_all_settings(db)
    override = await queries.get_entity_settings_override(db, kind, entity_id)
    parent_ov: dict[str, str] = {}
    parent_row: dict | None = None
    if kind == "module":
        mod = await queries.get_website_module(db, entity_id)
        if mod:
            parent_row = await queries.get_monitored_website(db, int(mod["website_id"]))
            if parent_row:
                parent_ov = await queries.get_entity_settings_override(
                    db, "website", int(parent_row["id"])
                )
    entity_row = {"settings_override": json.dumps(override) if override else None}
    eff = queries.effective_monitor_for_entity(
        global_parsed, entity_row, parent_row=parent_row
    )
    m = META[key]
    if key in override:
        src = "своё"
    elif kind == "module" and key in parent_ov:
        src = "с сайта"
    else:
        src = "общее (глобальное)"
    if key == "quiet_hours":
        cur_disp = _pretty_quiet(json.dumps(eff.quiet_hours, ensure_ascii=False))
        global_raw = await _raw_value(db, key)
        et_disp = _pretty_quiet(global_raw)
    else:
        cur_val = getattr(eff, key, None)
        cur_disp = str(cur_val)
        et_disp = queries.DEFAULT_SETTINGS.get(key, "")
    parts = [
        f"Изменение ({ENTITY_KIND_LABELS.get(kind, kind)} id={entity_id}): {m['title']}",
        "",
        m["body"],
        "",
        f"Сейчас ({src}): {cur_disp}",
        f"Глобальный эталон: {et_disp}",
        "",
        "Что ввести:",
        m["input"],
        "",
        "Отправьте следующим сообщением новое значение.",
    ]
    return "\n".join(parts)
