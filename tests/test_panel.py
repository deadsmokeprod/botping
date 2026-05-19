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


def test_parse_panel_ids_json() -> None:
    from botping.db.queries import _parse_panel_ids_json

    assert _parse_panel_ids_json('[]') == []
    assert _parse_panel_ids_json('[1, 2, 2]') == [1, 2, 2]
    assert _parse_panel_ids_json(None) == []
    assert _parse_panel_ids_json('invalid') == []
