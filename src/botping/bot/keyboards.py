from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botping.bot import copy as ui
from botping.bot.settings_help import (
    ENTITY_SETTINGS_GROUPS,
    META,
    SETTINGS_GROUP_KEYS,
    SETTINGS_GROUPS,
    setting_button_label,
)


def _back_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=ui.BTN_BACK, callback_data="nav:back")]


def with_back(markup: InlineKeyboardMarkup, *, show: bool = True) -> InlineKeyboardMarkup:
    rows = [list(r) for r in markup.inline_keyboard]
    if show:
        rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def screen_with_back(inner: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Статус/диск/сбои: кнопки раздела + назад (без дубля главного меню)."""
    rows = [list(r) for r in inner.inline_keyboard]
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ui.BTN_MAIN, callback_data="menu:main")],
        ]
    )


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ui.BTN_MAIN, callback_data="menu:main")],
            _back_row(),
        ]
    )


def cancel_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="nav:back")],
        ]
    )


def setting_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="nav:back")],
        ]
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статус", callback_data="menu:status"),
                InlineKeyboardButton(text="⚠️ Сбои", callback_data="menu:failures"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
                InlineKeyboardButton(text="🤖 Боты", callback_data="menu:bots"),
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Роутеры",
                    callback_data="menu:routers",
                ),
                InlineKeyboardButton(
                    text="🌍 Сайты",
                    callback_data="menu:websites",
                ),
            ],
            [
                InlineKeyboardButton(text="📈 Excel", callback_data="menu:report_excel"),
                InlineKeyboardButton(text="💾 Диск", callback_data="menu:disk"),
            ],
            [
                InlineKeyboardButton(text=ui.BTN_HELP, callback_data="menu:help"),
            ],
        ]
    )


def report_cancel_keyboard() -> InlineKeyboardMarkup:
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="report:cancel")],
            ]
        )
    )


def settings_menu() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🏷 Наименования", callback_data="names:menu")],
    ]
    for gid, g in SETTINGS_GROUPS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{g['emoji']} {g['title']}",
                    callback_data=f"setgrp:{gid}",
                )
            ]
        )
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def settings_group_menu(group_id: str) -> InlineKeyboardMarkup:
    keys = SETTINGS_GROUP_KEYS.get(group_id, [])
    rows: list[list[InlineKeyboardButton]] = []
    for key in keys:
        if key not in META:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=setting_button_label(key),
                    callback_data=f"set:{key}",
                )
            ]
        )
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def entity_settings_menu(kind: str, entity_id: int, override_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🌐 Глобальные настройки (дефолты)",
                callback_data="menu:settings",
            )
        ],
        [
            InlineKeyboardButton(
                text="📡 Мониторинг и алерты",
                callback_data=f"eset:grp:monitor:{kind}:{entity_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🔔 Уведомления",
                callback_data=f"eset:grp:notify:{kind}:{entity_id}",
            )
        ],
    ]
    if override_count:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Сбросить все к общим",
                    callback_data=f"eset:resetall:{kind}:{entity_id}",
                )
            ]
        )
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def entity_settings_group_menu(
    kind: str, entity_id: int, group_id: str, overridden: set[str]
) -> InlineKeyboardMarkup:
    keys = ENTITY_SETTINGS_GROUPS.get(group_id, [])
    rows: list[list[InlineKeyboardButton]] = []
    for key in keys:
        if key not in META:
            continue
        mark = "✎ " if key in overridden else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{setting_button_label(key)}",
                    callback_data=f"eset:key:{key}:{kind}:{entity_id}",
                )
            ]
        )
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def entity_setting_detail_keyboard(
    kind: str, entity_id: int, key: str, is_overridden: bool
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data=f"eset:edit:{key}:{kind}:{entity_id}",
            )
        ],
    ]
    if is_overridden:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Сбросить этот параметр",
                    callback_data=f"eset:resetkey:{key}:{kind}:{entity_id}",
                )
            ]
        )
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def entity_setting_input_keyboard(kind: str, entity_id: int, key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="nav:back")],
        ]
    )


def names_category_menu() -> InlineKeyboardMarkup:
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Боты", callback_data="names:cat:bot")],
                [InlineKeyboardButton(text="🌐 Роутеры", callback_data="names:cat:router")],
                [InlineKeyboardButton(text="📡 Устройства LAN", callback_data="names:cat:target")],
                [InlineKeyboardButton(text="🌍 Сайты", callback_data="names:cat:website")],
                [InlineKeyboardButton(text="📦 Модули сайтов", callback_data="names:cat:module")],
            ]
        )
    )


def names_entity_list(
    kind: str,
    entity_rows: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for eid, label in entity_rows:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"names:edit:{kind}:{eid}",
                )
            ]
        )
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def names_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="names:cancel")],
        ]
    )


def bots_menu(bot_rows: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for bid, name, en in bot_rows:
        flag = "🟢" if en else "⚪"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{flag} {name}",
                    callback_data=f"bot:view:{bid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить бота", callback_data="bot:add")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def bot_detail(bot_id: int, enabled: bool, override_count: int = 0) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "✅ Включить"
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Настройки",
                        callback_data=f"eset:home:bot:{bot_id}",
                    )
                ],
                [InlineKeyboardButton(text="📋 Сниппет", callback_data=f"bot:snippet:{bot_id}")],
                [
                    InlineKeyboardButton(text="🔑 Секрет", callback_data=f"bot:secret:{bot_id}"),
                    InlineKeyboardButton(text="🔄 Сменить", callback_data=f"bot:rotate:{bot_id}"),
                ],
                [InlineKeyboardButton(text=toggle, callback_data=f"bot:toggle:{bot_id}")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"bot:delask:{bot_id}")],
            ]
        )
    )


def confirm_delete(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"bot:del:{bot_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"bot:view:{bot_id}"),
            ],
        ]
    )


def routers_menu(router_rows: list[tuple[int, str]]) -> InlineKeyboardMarkup:
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
    rows.append([InlineKeyboardButton(text="➕ Добавить роутер", callback_data="site:add")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def sites_menu(router_rows: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    labels = [(rid, f"{name}") for rid, name, _en in router_rows]
    return routers_menu(labels)


def router_after_create(router_id: int) -> InlineKeyboardMarkup:
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Устройство (шаг 2)",
                        callback_data=f"site:target_add:{router_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🌐 К роутеру",
                        callback_data=f"site:view:{router_id}",
                    )
                ],
                [InlineKeyboardButton(text="📋 К списку", callback_data="menu:routers")],
            ]
        )
    )


def target_added_more(router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Ещё одно",
                    callback_data=f"site:target_add:{router_id}",
                ),
                InlineKeyboardButton(
                    text="🌐 К роутеру",
                    callback_data=f"site:view:{router_id}",
                ),
            ],
        ]
    )


def router_detail(
    router_id: int,
    enabled: bool,
    target_buttons: list[tuple[int, str]],
    override_count: int = 0,
) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "✅ Включить"
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
                text="➕ Устройство в LAN",
                callback_data=f"site:target_add:{router_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data=f"eset:home:router:{router_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔧 MikroTik",
                callback_data=f"site:setup:{router_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="📶 WAN/LTE",
                callback_data=f"site:uplink:{router_id}",
            ),
            InlineKeyboardButton(
                text="📜 Журнал",
                callback_data=f"site:evlog:{router_id}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="🔑 Секрет", callback_data=f"site:secret:{router_id}"),
            InlineKeyboardButton(text="🔄 Сменить", callback_data=f"site:rotate:{router_id}"),
        ]
    )
    rows.append([InlineKeyboardButton(text=toggle, callback_data=f"site:toggle:{router_id}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"site:delask:{router_id}")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def site_detail(router_id: int, enabled: bool) -> InlineKeyboardMarkup:
    return router_detail(router_id, enabled, [])


def site_target_detail(target_id: int, router_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "✅ Включить"
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Настройки",
                        callback_data=f"eset:home:target:{target_id}",
                    )
                ],
                [InlineKeyboardButton(text=toggle, callback_data=f"site:ttoggle:{target_id}")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"site:tdelask:{target_id}")],
            ]
        )
    )


def confirm_site_delete(router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"site:del:{router_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"site:view:{router_id}"),
            ],
        ]
    )


def confirm_target_delete(target_id: int, router_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"site:tdel:{target_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"site:tview:{target_id}"),
            ],
        ]
    )


def websites_menu(website_rows: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for wid, label in website_rows:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"web:view:{wid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить сайт", callback_data="web:add")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def website_after_create(website_id: int) -> InlineKeyboardMarkup:
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Модуль (шаг 2)",
                        callback_data=f"web:mod_add:{website_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🌍 К сайту",
                        callback_data=f"web:view:{website_id}",
                    )
                ],
                [InlineKeyboardButton(text="📋 К списку", callback_data="menu:websites")],
            ]
        )
    )


def website_detail(
    website_id: int,
    enabled: bool,
    module_buttons: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "✅ Включить"
    rows: list[list[InlineKeyboardButton]] = []
    for mid, label in module_buttons:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"web:mview:{mid}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Модуль",
                callback_data=f"web:mod_add:{website_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data=f"eset:home:website:{website_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔧 Установка агента",
                callback_data=f"web:setup:{website_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="🔑 Секрет", callback_data=f"web:secret:{website_id}"),
            InlineKeyboardButton(text="🔄 Сменить", callback_data=f"web:rotate:{website_id}"),
        ]
    )
    rows.append([InlineKeyboardButton(text=toggle, callback_data=f"web:toggle:{website_id}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"web:delask:{website_id}")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=rows))


def website_module_detail(module_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "✅ Включить"
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Настройки",
                        callback_data=f"eset:home:module:{module_id}",
                    )
                ],
                [InlineKeyboardButton(text=toggle, callback_data=f"web:mtoggle:{module_id}")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"web:mdelask:{module_id}")],
            ]
        )
    )


def confirm_website_delete(website_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"web:del:{website_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"web:view:{website_id}"),
            ],
        ]
    )


def confirm_webmod_delete(module_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"web:mdel:{module_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"web:mview:{module_id}"),
            ],
        ]
    )
