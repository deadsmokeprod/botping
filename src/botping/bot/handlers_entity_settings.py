from __future__ import annotations

import json
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot import panel_screens as screens
from botping.bot.panel import render_panel_message
from botping.bot.settings_help import META
from botping.bot.states import EntityQuietHoursStates, EntitySettingStates
from botping.db import queries
from botping.db.monitor_settings import MONITOR_OVERRIDE_KEYS
from botping.db.pool import Database


async def _entity_row(db: Database, kind: str, entity_id: int) -> dict | None:
    if kind == "bot":
        return await queries.get_monitored_bot(db, entity_id)
    if kind == "router":
        return await queries.get_monitored_router(db, entity_id)
    if kind == "target":
        return await queries.get_router_target(db, entity_id)
    if kind == "website":
        return await queries.get_monitored_website(db, entity_id)
    if kind == "module":
        return await queries.get_website_module(db, entity_id)
    return None


def register_entity_settings_handlers(router: Router) -> None:
    @router.callback_query(F.data.startswith("setgrp:"))
    async def on_setgrp(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        gid = cq.data.split(":", 1)[1]
        await state.set_state(None)
        await screens.goto_screen_cq(cq, state, db, f"setgrp:{gid}")
        await cq.answer()

    @router.callback_query(F.data.startswith("eset:"))
    async def on_eset(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        parts = cq.data.split(":")
        action = parts[1]
        if action == "home":
            kind, eid = parts[2], int(parts[3])
            await state.set_state(None)
            await screens.goto_screen_cq(cq, state, db, f"eset:home:{kind}:{eid}")
            await cq.answer()
            return
        if action == "grp":
            gid, kind, eid = parts[2], parts[3], int(parts[4])
            await state.set_state(None)
            await screens.goto_screen_cq(cq, state, db, f"eset:grp:{gid}:{kind}:{eid}")
            await cq.answer()
            return
        if action == "key":
            key, kind, eid = parts[2], parts[3], int(parts[4])
            await state.set_state(None)
            await screens.goto_screen_cq(cq, state, db, f"eset:key:{key}:{kind}:{eid}")
            await cq.answer()
            return
        if action == "edit":
            key, kind, eid = parts[2], parts[3], int(parts[4])
            if key not in MONITOR_OVERRIDE_KEYS:
                await cq.answer("❌ Неизвестный параметр", show_alert=True)
                return
            await state.update_data(eset_kind=kind, eset_id=eid, eset_key=key)
            if key == "quiet_hours":
                await state.set_state(EntityQuietHoursStates.waiting_json)
            else:
                await state.set_state(EntitySettingStates.waiting_value)
            await screens.goto_screen_cq(cq, state, db, f"eset:edit:{key}:{kind}:{eid}")
            await cq.answer()
            return
        if action == "resetkey":
            key, kind, eid = parts[2], parts[3], int(parts[4])
            uid = cq.from_user.id if cq.from_user else 0
            await queries.clear_entity_settings_key(db, kind, eid, key, admin_chat_id=uid)
            await screens.goto_screen_cq(
                cq,
                state,
                db,
                f"eset:grp:{'notify' if key == 'quiet_hours' else 'monitor'}:{kind}:{eid}",
                push=False,
                pop=1,
            )
            await cq.answer("Сброшено к общим")
            return
        if action == "resetall":
            kind, eid = parts[2], int(parts[3])
            uid = cq.from_user.id if cq.from_user else 0
            await queries.clear_entity_settings_all(db, kind, eid, admin_chat_id=uid)
            await screens.goto_screen_cq(cq, state, db, f"eset:home:{kind}:{eid}", push=False)
            await cq.answer("Все настройки — общие")
            return
        await cq.answer()

    @router.message(EntitySettingStates.waiting_value, F.text)
    async def on_entity_setting_value(
        message: Message, state: FSMContext, db: Database
    ) -> None:
        data = await state.get_data()
        kind = str(data.get("eset_kind") or "")
        eid = int(data.get("eset_id") or 0)
        key = str(data.get("eset_key") or "")
        if not kind or not eid or not key:
            await state.set_state(None)
            return
        raw = (message.text or "").strip()
        if not re.fullmatch(r"-?\d+", raw):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Нужно целое число. Повторите ввод.",
                reply_markup=kb.entity_setting_input_keyboard(kind, eid, key),
            )
            return
        uid = message.from_user.id if message.from_user else 0
        await queries.set_entity_settings_key(db, kind, eid, key, str(int(raw)), admin_chat_id=uid)
        await state.set_state(None)
        await screens.goto_screen_message(
            message,
            state,
            db,
            f"eset:home:{kind}:{eid}",
            push=False,
            pop=1,
        )

    @router.message(EntityQuietHoursStates.waiting_json, F.text)
    async def on_entity_quiet_json(
        message: Message, state: FSMContext, db: Database
    ) -> None:
        data = await state.get_data()
        kind = str(data.get("eset_kind") or "")
        eid = int(data.get("eset_id") or 0)
        key = str(data.get("eset_key") or "quiet_hours")
        raw = (message.text or "").strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Невалидный JSON. Повторите или отправьте {}",
                reply_markup=kb.entity_setting_input_keyboard(kind, eid, key),
            )
            return
        if obj != {} and not isinstance(obj, dict):
            await render_panel_message(
                message,
                state,
                db,
                "⚠️ Нужен объект JSON или {}.",
                reply_markup=kb.entity_setting_input_keyboard(kind, eid, key),
            )
            return
        uid = message.from_user.id if message.from_user else 0
        await queries.set_entity_settings_key(
            db,
            kind,
            eid,
            key,
            json.dumps(obj, ensure_ascii=False),
            admin_chat_id=uid,
        )
        await state.set_state(None)
        await screens.goto_screen_message(
            message, state, db, f"eset:home:{kind}:{eid}", push=False, pop=1
        )


async def _override_count(db: Database, kind: str, entity_id: int) -> int:
    row = await _entity_row(db, kind, entity_id)
    if not row:
        return 0
    return queries.count_override_keys(row.get("settings_override"))
