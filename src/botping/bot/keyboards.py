from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def commands_reply_keyboard() -> ReplyKeyboardMarkup:
    """Кнопки-команды внизу экрана (всегда видны после /start)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="/start"),
                KeyboardButton(text="/status"),
            ],
            [
                KeyboardButton(text="/failures"),
                KeyboardButton(text="/settings"),
            ],
            [KeyboardButton(text="/report")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Команда или текст…",
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Статус", callback_data="menu:status"),
                InlineKeyboardButton(text="Сбои", callback_data="menu:failures"),
            ],
            [
                InlineKeyboardButton(text="Настройки", callback_data="menu:settings"),
                InlineKeyboardButton(text="Боты", callback_data="menu:bots"),
            ],
            [
                InlineKeyboardButton(
                    text="Роутеры и устройства",
                    callback_data="menu:routers",
                ),
            ],
            [
                InlineKeyboardButton(text="Отчёт Excel", callback_data="menu:report_excel"),
                InlineKeyboardButton(text="Диск", callback_data="menu:disk"),
            ],
        ]
    )


def report_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="report:cancel")],
        ]
    )


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Интервал проверки (с)", callback_data="set:check_interval_sec")],
            [InlineKeyboardButton(text="Таймаут запроса (с)", callback_data="set:request_timeout_sec")],
            [InlineKeyboardButton(text="Порог падений подряд", callback_data="set:fail_threshold")],
            [
                InlineKeyboardButton(
                    text="Повтор алерта при down (с)",
                    callback_data="set:repeat_alert_interval_sec",
                )
            ],
            [InlineKeyboardButton(text="Порог медленного ответа (мс, 0=выкл)", callback_data="set:slow_ms")],
            [
                InlineKeyboardButton(
                    text="Ежедневный Excel 00:00 МСК (0/1)",
                    callback_data="set:daily_excel_report_enabled",
                )
            ],
            [InlineKeyboardButton(text="Тихие часы (JSON)", callback_data="set:quiet_hours")],
            [
                InlineKeyboardButton(
                    text="Таймаут heartbeat (с)",
                    callback_data="set:heartbeat_timeout_sec",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Лимит чужих запросов/мин",
                    callback_data="set:heartbeat_unauth_rate_per_min",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Порог неудач до бана",
                    callback_data="set:heartbeat_ban_fails_threshold",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Длительность бана (мин)",
                    callback_data="set:heartbeat_ban_duration_min",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверка Telegram API (0/1)",
                    callback_data="set:telegram_api_probe_enabled",
                )
            ],
            [InlineKeyboardButton(text="Порог очистки диска (%)", callback_data="set:disk_usage_threshold_pct")],
            [InlineKeyboardButton(text="Интервал проверки диска (с)", callback_data="set:disk_check_interval_sec")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
        ]
    )


def bots_menu(bot_rows: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for bid, name, en in bot_rows:
        flag = "on" if en else "off"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{name} [{flag}]",
                    callback_data=f"bot:view:{bid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="+ Добавить бота", callback_data="bot:add")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bot_detail(bot_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "Выключить" if enabled else "Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Показать сниппет", callback_data=f"bot:snippet:{bot_id}")],
            [
                InlineKeyboardButton(text="Показать секрет", callback_data=f"bot:secret:{bot_id}"),
                InlineKeyboardButton(text="Сменить секрет", callback_data=f"bot:rotate:{bot_id}"),
            ],
            [InlineKeyboardButton(text=toggle, callback_data=f"bot:toggle:{bot_id}")],
            [InlineKeyboardButton(text="Удалить", callback_data=f"bot:delask:{bot_id}")],
            [InlineKeyboardButton(text="К списку ботов", callback_data="menu:bots")],
        ]
    )


def confirm_delete(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"bot:del:{bot_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"bot:view:{bot_id}"),
            ],
        ]
    )


def routers_menu(router_rows: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """router_rows: (id, button label with status summary)."""
    rows: list[list[InlineKeyboardButton]] = []
    for rid, label in router_rows:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"site:view:{rid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="+ Добавить роутер", callback_data="site:add")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sites_menu(router_rows: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    """Backward-compatible wrapper."""
    labels = [(rid, f"{name} [{'on' if en else 'off'}]") for rid, name, en in router_rows]
    return routers_menu(labels)


def router_after_create(router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="+ Устройство (шаг 2)",
                    callback_data=f"site:target_add:{router_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="К роутеру (шаг 3 позже)",
                    callback_data=f"site:view:{router_id}",
                )
            ],
            [InlineKeyboardButton(text="К списку", callback_data="menu:routers")],
        ]
    )


def target_added_more(router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, ещё одно",
                    callback_data=f"site:target_add:{router_id}",
                ),
                InlineKeyboardButton(
                    text="Нет, к роутеру",
                    callback_data=f"site:view:{router_id}",
                ),
            ],
        ]
    )


def router_detail(
    router_id: int,
    enabled: bool,
    target_buttons: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    toggle = "Выключить мониторинг" if enabled else "Включить мониторинг"
    rows: list[list[InlineKeyboardButton]] = []
    for tid, label in target_buttons:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"site:tview:{tid}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="+ Устройство в LAN",
                callback_data=f"site:target_add:{router_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Установка на MikroTik",
                callback_data=f"site:setup:{router_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Переключение WAN/LTE",
                callback_data=f"site:uplink:{router_id}",
            ),
            InlineKeyboardButton(
                text="Журнал переключений",
                callback_data=f"site:evlog:{router_id}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="Секрет", callback_data=f"site:secret:{router_id}"),
            InlineKeyboardButton(text="Сменить секрет", callback_data=f"site:rotate:{router_id}"),
        ]
    )
    rows.append([InlineKeyboardButton(text=toggle, callback_data=f"site:toggle:{router_id}")])
    rows.append([InlineKeyboardButton(text="Удалить роутер", callback_data=f"site:delask:{router_id}")])
    rows.append([InlineKeyboardButton(text="К списку", callback_data="menu:routers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def site_detail(router_id: int, enabled: bool) -> InlineKeyboardMarkup:
    """Backward-compatible: no per-target buttons."""
    return router_detail(router_id, enabled, [])


def site_target_detail(target_id: int, router_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "Выключить" if enabled else "Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle, callback_data=f"site:ttoggle:{target_id}")],
            [InlineKeyboardButton(text="Удалить цель", callback_data=f"site:tdelask:{target_id}")],
            [InlineKeyboardButton(text="К роутеру", callback_data=f"site:view:{router_id}")],
            [InlineKeyboardButton(text="К списку", callback_data="menu:routers")],
        ]
    )


def confirm_site_delete(router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"site:del:{router_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"site:view:{router_id}"),
            ],
        ]
    )


def confirm_target_delete(target_id: int, router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"site:tdel:{target_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"site:tview:{target_id}"),
            ],
        ]
    )
