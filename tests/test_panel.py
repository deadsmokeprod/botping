from __future__ import annotations

import pytest

from botping.bot.panel import merge_panel_ids


def test_merge_panel_ids_unique_order() -> None:
    assert merge_panel_ids([1, 2], [2, 3], [1]) == [1, 2, 3]


def test_merge_panel_ids_empty() -> None:
    assert merge_panel_ids([], []) == []


@pytest.mark.asyncio
async def test_push_pop_nav() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from botping.bot.panel import KEY_CURRENT_SCREEN, KEY_NAV_STACK, pop_nav, push_nav

    state = MagicMock()
    storage: dict = {KEY_NAV_STACK: [], KEY_CURRENT_SCREEN: "main"}

    async def get_data() -> dict:
        return dict(storage)

    async def update_data(**kwargs: object) -> None:
        storage.update(kwargs)

    state.get_data = AsyncMock(side_effect=get_data)
    state.update_data = AsyncMock(side_effect=update_data)

    await push_nav(state, "bots")
    assert storage[KEY_CURRENT_SCREEN] == "bots"
    assert storage[KEY_NAV_STACK] == ["main"]

    await push_nav(state, "bot:1")
    assert storage[KEY_CURRENT_SCREEN] == "bot:1"
    assert storage[KEY_NAV_STACK] == ["main", "bots"]

    prev = await pop_nav(state)
    assert prev == "bots"
    assert storage[KEY_CURRENT_SCREEN] == "bots"
    assert storage[KEY_NAV_STACK] == ["main"]

    prev2 = await pop_nav(state)
    assert prev2 == "main"
    assert storage[KEY_NAV_STACK] == []


@pytest.mark.asyncio
async def test_pop_nav_n_and_clear_nav() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from botping.bot.panel import (
        KEY_CURRENT_SCREEN,
        KEY_NAV_STACK,
        clear_nav,
        pop_nav_n,
        push_nav,
    )

    state = MagicMock()
    storage: dict = {KEY_NAV_STACK: [], KEY_CURRENT_SCREEN: "main"}

    async def get_data() -> dict:
        return dict(storage)

    async def update_data(**kwargs: object) -> None:
        storage.update(kwargs)

    state.get_data = AsyncMock(side_effect=get_data)
    state.update_data = AsyncMock(side_effect=update_data)

    await push_nav(state, "settings")
    await push_nav(state, "setgrp:monitor")
    assert storage[KEY_NAV_STACK] == ["main", "settings"]

    screen = await pop_nav_n(state, 2)
    assert screen == "main"
    assert storage[KEY_CURRENT_SCREEN] == "main"
    assert storage[KEY_NAV_STACK] == []

    await push_nav(state, "bots")
    await clear_nav(state)
    assert storage[KEY_CURRENT_SCREEN] == "main"
    assert storage[KEY_NAV_STACK] == []


@pytest.mark.asyncio
async def test_apply_screen_nav_forward_and_up() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from botping.bot.panel import (
        KEY_CURRENT_SCREEN,
        KEY_NAV_STACK,
        apply_screen_nav,
        pop_nav,
        push_nav,
    )

    state = MagicMock()
    storage: dict = {KEY_NAV_STACK: [], KEY_CURRENT_SCREEN: "main"}

    async def get_data() -> dict:
        return dict(storage)

    async def update_data(**kwargs: object) -> None:
        storage.update(kwargs)

    state.get_data = AsyncMock(side_effect=get_data)
    state.update_data = AsyncMock(side_effect=update_data)

    await apply_screen_nav(state, "settings", push=True)
    await apply_screen_nav(state, "setgrp:monitor", push=True)
    assert storage[KEY_NAV_STACK] == ["main", "settings"]

    await apply_screen_nav(state, "settings", push=False, pop=1)
    assert storage[KEY_CURRENT_SCREEN] == "settings"
    assert storage[KEY_NAV_STACK] == ["main"]

    prev = await pop_nav(state)
    assert prev == "main"
    assert storage[KEY_NAV_STACK] == []


@pytest.mark.asyncio
async def test_apply_screen_nav_main_clears_stack() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from botping.bot.panel import (
        KEY_CURRENT_SCREEN,
        KEY_NAV_STACK,
        apply_screen_nav,
        push_nav,
    )

    state = MagicMock()
    storage: dict = {KEY_NAV_STACK: [], KEY_CURRENT_SCREEN: "main"}

    async def get_data() -> dict:
        return dict(storage)

    async def update_data(**kwargs: object) -> None:
        storage.update(kwargs)

    state.get_data = AsyncMock(side_effect=get_data)
    state.update_data = AsyncMock(side_effect=update_data)

    await push_nav(state, "report_prompt")
    await apply_screen_nav(state, "main", push=False)
    assert storage[KEY_NAV_STACK] == []


def test_settings_group_menu_has_nav_back_only() -> None:
    from botping.bot.keyboards import settings_group_menu

    markup = settings_group_menu("monitor")
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "nav:back" in callbacks
    assert "menu:settings" not in callbacks


def test_parse_panel_ids_json() -> None:
    from botping.db.queries import _parse_panel_ids_json

    assert _parse_panel_ids_json('[]') == []
    assert _parse_panel_ids_json('[1, 2, 2]') == [1, 2, 2]
    assert _parse_panel_ids_json(None) == []
    assert _parse_panel_ids_json('invalid') == []
