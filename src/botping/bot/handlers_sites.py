from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot.common import (
    chunk_text,
    format_age_ru,
    heartbeat_age_sec,
    mask_secret,
    public_host_from_env,
)
from botping.bot.states import AddRouterStates, AddTargetStates
from botping.bot.ui import edit_or_answer
from botping.db import queries
from botping.db.pool import Database, generate_heartbeat_secret
from botping.heartbeat_server import HeartbeatServer
from botping.mikrotik.snippet import build_routeros_snippet


def _target_status_line(t: dict, hb_timeout: int) -> str:
    from botping.monitor.router_monitor import _target_alive

    alive, err = _target_alive(t, hb_timeout)
    if alive:
        ms = t.get("last_latency_ms")
        ms_s = f", {ms} ms" if ms is not None else ""
        return f"ЖИВ{ms_s}"
    return f"НЕДОСТУПЕН ({err or '?'})"


async def _format_site_detail(db: Database, router_id: int) -> str | None:
    r = await queries.get_monitored_router(db, router_id)
    if not r:
        return None
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    age = heartbeat_age_sec(r)
    if age is None:
        hb_state = "нет ни одного heartbeat"
    elif age <= hb_timeout:
        hb_state = f"ЖИВ, пинг {format_age_ru(age)} назад"
    else:
        hb_state = f"НЕДОСТУПЕН, нет пинга {format_age_ru(age)}"
    lines = [
        f"Роутер: {r['display_name']}",
        f"id={router_id}",
        f"Статус: {'вкл' if r['enabled'] else 'выкл'}",
        f"Heartbeat: {hb_state}",
        f"Последний пинг: {r.get('last_heartbeat_at') or '—'}",
        f"С IP: {r.get('last_heartbeat_ip') or '—'}",
        f"Секрет: {mask_secret(str(r['heartbeat_secret']))}",
        f"URL: {public_host_from_env()}/heartbeat",
        "",
        "Цели LAN:",
    ]
    targets = await queries.list_router_targets(db, router_id)
    if not targets:
        lines.append("  (нет — добавьте «+ Цель»)")
    else:
        for t in targets:
            tid = int(t["id"])
            inc = await queries.get_open_router_target_incident(db, tid)
            inc_s = " ИНЦИДЕНТ" if inc else ""
            st = "вкл" if t["enabled"] else "выкл"
            lines.append(
                f"  · [{tid}] {t['display_name']} {t['address']} ({st}): "
                f"{_target_status_line(t, hb_timeout)}{inc_s}"
            )
    lines.append("")
    lines.append("После изменения целей обновите script на MikroTik («Показать сниппет»).")
    return "\n".join(lines)


def register_sites_handlers(router: Router) -> None:
    @router.callback_query(F.data == "menu:sites")
    async def on_menu_sites(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.clear()
        rows = await queries.list_monitored_routers(db)
        site_rows = [(int(r["id"]), str(r["display_name"]), bool(r["enabled"])) for r in rows]
        await edit_or_answer(
            cq,
            "Сайты (MikroTik + LAN):\n"
            "Роутер шлёт heartbeat и результаты ping по IP в вашей сети.",
            reply_markup=kb.sites_menu(site_rows),
        )
        await cq.answer()

    @router.callback_query(F.data == "site:add")
    async def on_site_add(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AddRouterStates.waiting_name)
        await cq.message.answer("Введите имя роутера / площадки (например «Офис»).")
        await cq.answer()

    @router.message(AddRouterStates.waiting_name, F.text)
    async def on_add_router_name(
        message: Message, state: FSMContext, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        name = (message.text or "").strip()
        if not name:
            await message.answer("Имя не может быть пустым.")
            return
        secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, secret):
            secret = generate_heartbeat_secret()
        new_id = await queries.insert_monitored_router(db, name, secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await state.clear()
        await message.answer(
            f"Роутер добавлен: {name} (id={new_id}).\n"
            "Добавьте LAN-цели и установите сниппет на MikroTik.",
            reply_markup=kb.main_menu(),
        )
        snippet = build_routeros_snippet(public_host_from_env(), secret, [])
        for part in chunk_text(
            "Сниппет MikroTik (после добавления целей запросите снова из карточки роутера):\n\n"
            + snippet
        ):
            await message.answer(part)

    @router.callback_query(F.data.startswith("site:view:"))
    async def on_site_view(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        text = await _format_site_detail(db, rid)
        if not text:
            await cq.answer("Не найден", show_alert=True)
            return
        r = await queries.get_monitored_router(db, rid)
        assert r is not None
        await edit_or_answer(cq, text, reply_markup=kb.site_detail(rid, bool(r["enabled"])))
        await cq.answer()

    @router.callback_query(F.data.startswith("site:snippet:"))
    async def on_site_snippet(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        targets = await queries.list_router_targets(db, rid)
        snippet = build_routeros_snippet(
            public_host_from_env(), str(r["heartbeat_secret"]), targets
        )
        await cq.message.answer(
            f"Сниппет для {r['display_name']}. System → Scripts → botping-lan, затем Scheduler 30с."
        )
        for part in chunk_text(snippet):
            await cq.message.answer(f"<pre>{part}</pre>", parse_mode="HTML")
        await cq.answer()

    @router.callback_query(F.data.startswith("site:secret:"))
    async def on_site_secret(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await cq.message.answer(
            f"Секрет роутера {r['display_name']} (id={rid}):\n"
            f"<code>{r['heartbeat_secret']}</code>",
            parse_mode="HTML",
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:rotate:"))
    async def on_site_rotate(
        cq: CallbackQuery, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        old = str(r["heartbeat_secret"])
        new_secret = generate_heartbeat_secret()
        while await queries.heartbeat_secret_in_use(db, new_secret, exclude=old):
            new_secret = generate_heartbeat_secret()
        await queries.regenerate_router_heartbeat_secret(db, rid, new_secret)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        await cq.message.answer(
            f"Новый секрет для {r['display_name']}:\n<code>{new_secret}</code>\n"
            "Обновите script на MikroTik.",
            parse_mode="HTML",
        )
        await cq.answer("Секрет обновлён")

    @router.callback_query(F.data.startswith("site:toggle:"))
    async def on_site_toggle(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await queries.update_router_enabled(db, rid, not bool(r["enabled"]))
        text = await _format_site_detail(db, rid)
        assert text is not None
        r2 = await queries.get_monitored_router(db, rid)
        assert r2 is not None
        await edit_or_answer(cq, text, reply_markup=kb.site_detail(rid, bool(r2["enabled"])))
        await cq.answer()

    @router.callback_query(F.data.startswith("site:delask:"))
    async def on_site_delask(cq: CallbackQuery, db: Database) -> None:
        rid = int(cq.data.split(":")[2])
        r = await queries.get_monitored_router(db, rid)
        if not r:
            await cq.answer("Не найден", show_alert=True)
            return
        await edit_or_answer(
            cq,
            f"Удалить роутер «{r['display_name']}» и все цели?",
            reply_markup=kb.confirm_site_delete(rid),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:del:"))
    async def on_site_del(
        cq: CallbackQuery, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        rid = int(cq.data.split(":")[2])
        await queries.delete_monitored_router(db, rid)
        if hb_server is not None:
            hb_server.invalidate_secrets_cache()
        rows = await queries.list_monitored_routers(db)
        site_rows = [(int(r["id"]), str(r["display_name"]), bool(r["enabled"])) for r in rows]
        await edit_or_answer(cq, "Роутер удалён.", reply_markup=kb.sites_menu(site_rows))
        await cq.answer()

    @router.callback_query(F.data.startswith("site:target_add:"))
    async def on_target_add(cq: CallbackQuery, state: FSMContext) -> None:
        rid = int(cq.data.split(":")[2])
        await state.update_data(target_router_id=rid)
        await state.set_state(AddTargetStates.waiting_name)
        await cq.message.answer("Имя цели (например «Камера вход»):")
        await cq.answer()

    @router.message(AddTargetStates.waiting_name, F.text)
    async def on_target_name(message: Message, state: FSMContext) -> None:
        name = (message.text or "").strip()
        if not name:
            await message.answer("Имя не может быть пустым.")
            return
        await state.update_data(target_name=name)
        await state.set_state(AddTargetStates.waiting_address)
        await message.answer("IP или hostname в LAN (например 192.168.88.10):")

    @router.message(AddTargetStates.waiting_address, F.text)
    async def on_target_address(message: Message, state: FSMContext, db: Database) -> None:
        addr = (message.text or "").strip()
        data = await state.get_data()
        rid = int(data.get("target_router_id") or 0)
        name = str(data.get("target_name") or "").strip()
        if not rid or not name or not addr:
            await state.clear()
            await message.answer("Сессия сброшена.")
            return
        if not re.match(r"^[\w.\-:/]+$", addr, re.ASCII):
            await message.answer("Недопустимый адрес. Используйте IP или hostname.")
            return
        try:
            tid = await queries.insert_router_target(db, rid, name, addr)
        except Exception:
            await message.answer("Не удалось добавить (возможно, такой адрес уже есть у этого роутера).")
            return
        await state.clear()
        await message.answer(
            f"Цель добавлена: {name} ({addr}), id={tid}.\n"
            "Обновите сниппет на MikroTik: Сайты → роутер → «Показать сниппет».",
            reply_markup=kb.main_menu(),
        )

    @router.callback_query(F.data.startswith("site:tview:"))
    async def on_target_view(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        rid = int(t["router_id"])
        settings = await queries.load_all_settings(db)
        hb_timeout = int(settings["heartbeat_timeout_sec"])
        text = (
            f"Цель: {t['display_name']}\n"
            f"id={tid}, router_id={rid}\n"
            f"Адрес: {t['address']}\n"
            f"Статус: {_target_status_line(t, hb_timeout)}\n"
            f"Последний отчёт: {t.get('last_ok_at') or '—'}"
        )
        await edit_or_answer(
            cq, text, reply_markup=kb.site_target_detail(tid, rid, bool(t["enabled"]))
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:ttoggle:"))
    async def on_target_toggle(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        await queries.update_router_target_enabled(db, tid, not bool(t["enabled"]))
        await on_target_view(cq, db)

    @router.callback_query(F.data.startswith("site:tdelask:"))
    async def on_target_delask(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        await edit_or_answer(
            cq,
            f"Удалить цель «{t['display_name']}» ({t['address']})?",
            reply_markup=kb.confirm_target_delete(tid, int(t["router_id"])),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("site:tdel:"))
    async def on_target_del(cq: CallbackQuery, db: Database) -> None:
        tid = int(cq.data.split(":")[2])
        t = await queries.get_router_target(db, tid)
        if not t:
            await cq.answer("Не найден", show_alert=True)
            return
        rid = int(t["router_id"])
        await queries.delete_router_target(db, tid)
        text = await _format_site_detail(db, rid)
        assert text is not None
        r = await queries.get_monitored_router(db, rid)
        assert r is not None
        await edit_or_answer(cq, text, reply_markup=kb.site_detail(rid, bool(r["enabled"])))
        await cq.answer()
