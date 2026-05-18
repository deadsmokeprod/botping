from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from botping.bot import keyboards as kb
from botping.bot.reports.build import build_availability_report_bundle
from botping.bot.reports.period_parse import parse_period_line
from botping.bot.settings_help import META, format_key_change_prompt
from botping.bot.handlers_sites import register_sites_handlers
from botping.bot.states import AddBotStates, QuietHoursStates, ReportStates, SettingStates
from botping.monitor.router_monitor import _target_alive
from botping.bot.ui import edit_or_answer
from botping.db import queries
from botping.db.pool import Database, generate_heartbeat_secret
from botping.heartbeat_server import HeartbeatServer
from botping.monitor.checker import check_getme
from botping.monitor.disk_guard import get_disk_info
from botping.timeutil import MOSCOW_TZ, now_moscow_naive

logger = logging.getLogger(__name__)


def _report_period_prompt_text() -> str:
    return (
        "📊 Отчёт Excel по доступности ботов\n\n"
        "Отправьте одной строкой период «от — до»:\n"
        "• 15.04.2026 по 20.04.2026\n"
        "• 15042025 по 20042025 — формат ДДММГГГГ (8 цифр на дату)\n"
        "• 150425 по 200425 — формат ДДММГГ (6 цифр, год 20ГГ)\n"
        "Можно разделить запятой: 15042025,20042025\n\n"
        "В файле: лист «Проверки» (каждый getMe), «Инциденты» (пересечение с периодом), "
        "«Аудит настроек», «Боты», «Настройки сейчас», «Сводка» и «Легенда».\n\n"
        "Лист «Проверки» — только строки за выбранный период. Если там пусто, "
        "а мониторинг только начали — выберите даты, когда процесс уже крутился и боты включены; "
        "на листе «Сводка» будет видно, за какой интервал вообще есть данные в базе.\n\n"
        "Даты и время везде по Москве (UTC+3, Europe/Moscow)."
    )


def mask_token(token: str) -> str:
    t = token.strip()
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}…{t[-4:]}"


def mask_secret(secret: str) -> str:
    s = (secret or "").strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}…{s[-4:]}"


def _format_age_ru(sec: int) -> str:
    if sec < 60:
        return f"{sec} с"
    if sec < 3600:
        return f"{sec // 60} мин"
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h} ч {m} мин" if m else f"{h} ч"


def _heartbeat_age_sec(bot: dict) -> int | None:
    raw = bot.get("last_heartbeat_at")
    if not raw:
        return None
    try:
        naive = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
        last = naive.replace(tzinfo=MOSCOW_TZ)
    except ValueError:
        return None
    now = datetime.now(MOSCOW_TZ)
    return max(0, int((now - last).total_seconds()))


def _public_host_from_env() -> str:
    """URL, на который боты будут слать heartbeat.

    Приоритет: переменная окружения BOTPING_PUBLIC_URL (если задана) →
    'http://<IP_VPS>:<port>' (IP вводится вручную при сомнении).
    """
    url = os.getenv("BOTPING_PUBLIC_URL", "").strip().rstrip("/")
    if url:
        return url
    host = os.getenv("BOTPING_PUBLIC_HOST", "").strip()
    port = os.getenv("HEARTBEAT_PORT", "8080").strip() or "8080"
    if host:
        return f"http://{host}:{port}"
    return f"http://<IP_VPS>:{port}"


def _heartbeat_snippet(secret: str) -> str:
    base = _public_host_from_env()
    return (
        "# --- Botping heartbeat (вставьте рядом с dp.start_polling) ---\n"
        "import asyncio, httpx\n"
        f"BOTPING_URL = \"{base}/heartbeat\"\n"
        f"HEARTBEAT_SECRET = \"{secret}\"\n"
        "\n"
        "async def _botping_heartbeat():\n"
        "    headers = {\"X-Heartbeat-Secret\": HEARTBEAT_SECRET}\n"
        "    async with httpx.AsyncClient(timeout=10) as client:\n"
        "        while True:\n"
        "            try:\n"
        "                await client.post(BOTPING_URL, headers=headers)\n"
        "            except Exception:\n"
        "                pass\n"
        "            await asyncio.sleep(30)\n"
        "\n"
        "# где-то после создания event loop, перед dp.start_polling(bot):\n"
        "# asyncio.create_task(_botping_heartbeat())\n"
    )


def _chunk_text(s: str, limit: int = 3900) -> list[str]:
    if len(s) <= limit:
        return [s]
    parts: list[str] = []
    cur = ""
    for line in s.splitlines():
        if len(cur) + len(line) + 1 > limit:
            if cur:
                parts.append(cur)
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur:
        parts.append(cur)
    return parts


async def _format_status(db: Database, hb_server: HeartbeatServer | None = None) -> str:
    bots = await queries.list_monitored_bots(db)
    settings = await queries.load_all_settings(db)
    hb_timeout = int(settings["heartbeat_timeout_sec"])
    tg_last = await queries.get_last_telegram_check(db)
    tg_inc = await queries.get_open_telegram_incident(db)

    lines: list[str] = []

    if not bots:
        lines.append("Нет ботов. Добавьте через «Боты» → «+ Добавить бота».")
    else:
        for b in bots:
            inc = await queries.get_open_incident(db, int(b["id"]))
            st = "вкл" if b["enabled"] else "выкл"
            age = _heartbeat_age_sec(b)
            inc_s = " ИНЦИДЕНТ" if inc else ""
            if age is None:
                state = "НЕТ ПИНГОВ (сниппет не вставлен или бот не запускался)"
            elif age <= hb_timeout:
                state = f"ЖИВ, последний пинг {_format_age_ru(age)} назад"
            else:
                state = f"НЕДОСТУПЕН, нет пинга уже {_format_age_ru(age)}"
            lines.append(f"- {b['display_name']} (id={b['id']}, {st}): {state}{inc_s}")

    lines.append("---")
    lines.append(f"Порог «живости» (таймаут пинга): {_format_age_ru(hb_timeout)}")

    if settings.get("telegram_api_probe_enabled", True):
        if not tg_last:
            tg_line = "Telegram API: проверок ещё не было"
        else:
            tg_ok = "ok" if tg_last["ok"] else "FAIL"
            tg_lat = tg_last["latency_ms"] if tg_last["latency_ms"] is not None else "?"
            tg_rl = " (rate_limit)" if tg_last.get("rate_limited") else ""
            tg_err = f" — {tg_last['error_text']}" if tg_last["error_text"] else ""
            tg_inc_s = " ИНЦИДЕНТ" if tg_inc else ""
            tg_line = (
                f"Telegram API: {tg_ok}{tg_rl}, {tg_last['ts']}, {tg_lat}ms{tg_err}{tg_inc_s}"
            )
        lines.append(tg_line)
    else:
        lines.append("Telegram API: проверка отключена (telegram_api_probe_enabled=0)")

    if hb_server is not None:
        try:
            st = hb_server.stats_snapshot()
            lines.append(
                f"Heartbeat защита: активных банов {st['active_bans']}, "
                f"заблокировано за сутки {st['blocked_24h']}"
            )
        except Exception:
            lines.append("Heartbeat защита: н/д")
    else:
        lines.append("Heartbeat защита: н/д")

    info = get_disk_info(db.path)
    ck_stats = await queries.get_checks_storage_stats(db)
    routers = await queries.list_monitored_routers(db)
    lines.append("---")
    lines.append("Роутеры и устройства (MikroTik):")
    if not routers:
        lines.append(
            "Нет роутеров. Добавьте через «Роутеры и устройства» → «+ Добавить роутер»."
        )
    else:
        for r in routers:
            rid = int(r["id"])
            st = "вкл" if r["enabled"] else "выкл"
            age = _heartbeat_age_sec(r)
            r_inc = await queries.get_open_router_incident(db, rid)
            inc_s = " ИНЦИДЕНТ" if r_inc else ""
            if age is None:
                r_state = "НЕТ ПИНГОВ"
            elif age <= hb_timeout:
                r_state = f"ЖИВ, пинг {_format_age_ru(age)} назад"
            else:
                r_state = f"НЕДОСТУПЕН, нет пинга {_format_age_ru(age)}"
            lines.append(f"- {r['display_name']} (id={rid}, {st}): {r_state}{inc_s}")
            if not r["enabled"]:
                continue
            targets = await queries.list_router_targets(db, rid, enabled_only=True)
            for t in targets:
                tid = int(t["id"])
                t_inc = await queries.get_open_router_target_incident(db, tid)
                t_inc_s = " ИНЦИДЕНТ" if t_inc else ""
                alive, terr = _target_alive(t, hb_timeout)
                if alive:
                    ms = t.get("last_latency_ms")
                    t_st = f"ЖИВ, {ms} ms" if ms is not None else "ЖИВ"
                else:
                    t_st = f"НЕДОСТУПЕН ({terr or '?'})"
                lines.append(f"  · {t['display_name']} {t['address']}: {t_st}{t_inc_s}")

    lines.append("---")
    lines.append(
        f"Диск: {info.used_pct:.0f}% ({info.used_gb:.1f}/{info.total_gb:.1f} ГБ), "
        f"БД: {info.db_size_mb:.1f} МБ, проверок: {ck_stats['count']}"
    )
    return "Статус:\n" + "\n".join(lines)


async def _format_disk_detail(db: Database) -> str:
    info = get_disk_info(db.path)
    ck_stats = await queries.get_checks_storage_stats(db)
    settings = await queries.load_all_settings(db)
    threshold = settings["disk_usage_threshold_pct"]
    return (
        "Дисковое пространство:\n"
        f"  Всего: {info.total_gb:.1f} ГБ\n"
        f"  Занято: {info.used_gb:.1f} ГБ ({info.used_pct:.1f}%)\n"
        f"  Свободно: {info.free_gb:.1f} ГБ\n"
        f"  Порог очистки: {threshold}%\n"
        f"\n"
        f"База данных:\n"
        f"  Размер файла: {info.db_size_mb:.1f} МБ\n"
        f"  Строк проверок: {ck_stats['count']}\n"
        f"  Самая старая: {ck_stats['min_ts'] or '—'}\n"
        f"  Самая новая: {ck_stats['max_ts'] or '—'}"
    )


def setup_router() -> Router:
    router = Router()

    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Botping: мониторинг ваших ботов через heartbeat.\n"
            "Каждый ваш бот сам раз в 30 секунд пингует Botping. Нет пинга — инцидент.\n"
            "Отдельно проверяется доступность Telegram API (getMe к admin-боту).\n"
            "Команды: /status, /failures, /settings, /report\n"
            "Отчёт Excel — кнопка «Отчёт Excel» или команда /report.\n"
            "Добавить бота: «Боты» → «+ Добавить бота».\n"
            "MikroTik + LAN: «Роутеры и устройства» → роутер → устройства (IP) → "
            "«Установка на MikroTik».",
            reply_markup=kb.main_menu(),
        )

    @router.message(Command("status"))
    async def cmd_status(
        message: Message, db: Database, hb_server: HeartbeatServer | None = None
    ) -> None:
        await message.answer(await _format_status(db, hb_server))

    @router.message(Command("failures"))
    async def cmd_failures(message: Message, command: CommandObject, db: Database) -> None:
        text = await _failures_text(db, command.args)
        for part in _chunk_text(text):
            await message.answer(part)

    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        await message.answer(
            "Настройки мониторинга.\n"
            "Выберите пункт — откроется описание, эталон и текущее значение; затем можно ввести новое.",
            reply_markup=kb.settings_menu(),
        )

    @router.message(Command("report"))
    async def cmd_report(message: Message, state: FSMContext) -> None:
        await state.set_state(ReportStates.waiting_period)
        await message.answer(_report_period_prompt_text(), reply_markup=kb.report_cancel_keyboard())

    @router.callback_query(F.data == "menu:main")
    async def on_menu_main(cq: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await edit_or_answer(cq, "Главное меню:", reply_markup=kb.main_menu())
        await cq.answer()

    @router.callback_query(F.data == "menu:status")
    async def on_menu_status(
        cq: CallbackQuery,
        state: FSMContext,
        db: Database,
        hb_server: HeartbeatServer | None = None,
    ) -> None:
        await state.clear()
        await edit_or_answer(
            cq, await _format_status(db, hb_server), reply_markup=kb.main_menu()
        )
        await cq.answer()

    @router.callback_query(F.data == "menu:failures")
    async def on_menu_failures(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.clear()
        t = await _failures_text(db, None)
        await edit_or_answer(cq, t[:4090], reply_markup=kb.main_menu())
        await cq.answer()

    @router.callback_query(F.data == "menu:settings")
    async def on_menu_settings(cq: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await edit_or_answer(
            cq,
            "Настройки мониторинга.\n"
            "Выберите пункт — откроется описание, эталон и текущее значение.",
            reply_markup=kb.settings_menu(),
        )
        await cq.answer()

    @router.callback_query(F.data == "menu:bots")
    async def on_menu_bots(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.clear()
        rows = await queries.list_monitored_bots(db)
        bot_rows = [(int(b["id"]), str(b["display_name"]), bool(b["enabled"])) for b in rows]
        await edit_or_answer(cq, "Мониторинг ботов:", reply_markup=kb.bots_menu(bot_rows))
        await cq.answer()

    @router.callback_query(F.data == "menu:disk")
    async def on_menu_disk(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        await state.clear()
        await edit_or_answer(cq, await _format_disk_detail(db), reply_markup=kb.main_menu())
        await cq.answer()

    @router.callback_query(F.data == "menu:report_excel")
    async def on_menu_report_excel(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ReportStates.waiting_period)
        await cq.message.answer(_report_period_prompt_text(), reply_markup=kb.report_cancel_keyboard())
        await cq.answer()

    @router.callback_query(F.data == "report:cancel")
    async def on_report_cancel(cq: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await edit_or_answer(cq, "Главное меню:", reply_markup=kb.main_menu())
        await cq.answer()

    @router.message(ReportStates.waiting_period, F.text)
    async def on_report_period(message: Message, state: FSMContext, db: Database) -> None:
        try:
            start, end = parse_period_line(message.text or "")
        except ValueError as e:
            await message.answer(str(e))
            return
        await message.answer("Собираю данные и формирую Excel…")
        try:
            bundle = await build_availability_report_bundle(db, start, end)
            await message.answer_document(
                BufferedInputFile(bundle.blob, filename=bundle.filename),
                caption=bundle.caption,
            )
        except Exception as e:
            logger.exception("excel report failed")
            hint = str(e).strip()
            if len(hint) > 280:
                hint = hint[:277] + "…"
            await message.answer(
                "Не удалось сформировать файл.\n"
                f"Текст ошибки: {hint}\n\n"
                "Если это про даты — используйте формат как в примере: 15.04.2026 по 20.04.2026"
            )
            return
        await state.clear()
        await message.answer("Готово.", reply_markup=kb.main_menu())

    @router.callback_query(F.data.startswith("set:"))
    async def on_set_click(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
        key = cq.data.split(":", 1)[1]
        if key not in META:
            await cq.answer("Неизвестный параметр.", show_alert=True)
            return
        await state.update_data(set_key=key)
        prompt = await format_key_change_prompt(db, key)
        for part in _chunk_text(prompt, 4000):
            await cq.message.answer(part)
        if key == "quiet_hours":
            await state.set_state(QuietHoursStates.waiting_json)
        else:
            await state.set_state(SettingStates.waiting_value)
        await cq.answer()

    @router.message(SettingStates.waiting_value, F.text)
    async def on_setting_value(message: Message, state: FSMContext, db: Database) -> None:
        data = await state.get_data()
        key = str(data.get("set_key") or "")
        if not key:
            await state.clear()
            await message.answer("Сессия сброшена. Откройте настройки снова.")
            return
        raw = (message.text or "").strip()
        if not re.fullmatch(r"-?\d+", raw):
            await message.answer("Нужно целое число. Повторите ввод или /settings.")
            return
        iv = int(raw)
        if key in ("daily_excel_report_enabled", "telegram_api_probe_enabled") and iv not in (0, 1):
            await message.answer("Для этого параметра допустимо только 0 (выкл) или 1 (вкл).")
            return
        value = str(iv)
        uid = message.from_user.id if message.from_user else 0
        await queries.set_setting(db, key, value, admin_chat_id=uid)
        await state.clear()
        await message.answer(
            f"Сохранено: {key} = {value}",
            reply_markup=kb.settings_menu(),
        )

    @router.message(QuietHoursStates.waiting_json, F.text)
    async def on_quiet_json(message: Message, state: FSMContext, db: Database) -> None:
        raw = (message.text or "").strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            await message.answer("Невалидный JSON. Повторите или отправьте {}")
            return
        if obj != {} and not isinstance(obj, dict):
            await message.answer("Нужен объект JSON или {}.")
            return
        uid = message.from_user.id if message.from_user else 0
        await queries.set_setting(db, "quiet_hours", json.dumps(obj, ensure_ascii=False), admin_chat_id=uid)
        await state.clear()
        await message.answer("Тихие часы обновлены.", reply_markup=kb.settings_menu())

    @router.callback_query(F.data.startswith("bot:view:"))
    async def on_bot_view(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("Не найден", show_alert=True)
            return
        token_m = mask_token(str(b["token"]))
        secret_m = mask_secret(str(b["heartbeat_secret"]))
        en = bool(b["enabled"])
        settings = await queries.load_all_settings(db)
        hb_timeout = int(settings["heartbeat_timeout_sec"])
        age = _heartbeat_age_sec(b)
        if age is None:
            hb_state = "нет ни одного пинга"
        elif age <= hb_timeout:
            hb_state = f"ЖИВ, пинг {_format_age_ru(age)} назад"
        else:
            hb_state = f"НЕДОСТУПЕН, нет пинга {_format_age_ru(age)}"
        last_hb = b.get("last_heartbeat_at") or "—"
        last_ip = b.get("last_heartbeat_ip") or "—"
        text = (
            f"Бот: {b['display_name']}\n"
            f"id={bid}\n"
            f"Токен: {token_m}\n"
            f"Статус: {'вкл' if en else 'выкл'}\n"
            f"Heartbeat: {hb_state}\n"
            f"Последний пинг: {last_hb}\n"
            f"С IP: {last_ip}\n"
            f"Секрет: {secret_m}\n"
            f"URL: {_public_host_from_env()}/heartbeat"
        )
        await edit_or_answer(cq, text, reply_markup=kb.bot_detail(bid, en))
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:snippet:"))
    async def on_bot_snippet(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("Не найден", show_alert=True)
            return
        secret = str(b["heartbeat_secret"])
        snippet = _heartbeat_snippet(secret)
        await cq.message.answer(
            "Сниппет для вашего бота (вставьте рядом с dp.start_polling).\n"
            "Если вместо `<IP_VPS>` у вас заглушка — задайте в .env переменную "
            "`BOTPING_PUBLIC_HOST=198.51.100.42` (или свой IP/домен) и перезапустите Botping."
        )
        await cq.message.answer(f"<pre>{snippet}</pre>", parse_mode="HTML")
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:secret:"))
    async def on_bot_secret(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("Не найден", show_alert=True)
            return
        secret = str(b["heartbeat_secret"])
        await cq.message.answer(
            f"Секрет бота {b['display_name']} (id={bid}):\n<code>{secret}</code>\n\n"
            "Не делитесь им. Любой, у кого он есть, сможет отправлять фальшивые пинги.",
            parse_mode="HTML",
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:rotate:"))
    async def on_bot_rotate(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("Не найден", show_alert=True)
            return
        new_secret = generate_heartbeat_secret()
        await queries.regenerate_heartbeat_secret(db, bid, new_secret)
        await cq.message.answer(
            f"Новый секрет для {b['display_name']} (id={bid}):\n<code>{new_secret}</code>\n\n"
            "Старый больше не работает. Обновите сниппет в коде бота и перезапустите его.",
            parse_mode="HTML",
        )
        await cq.answer("Секрет обновлён")

    @router.callback_query(F.data.startswith("bot:toggle:"))
    async def on_bot_toggle(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("Не найден", show_alert=True)
            return
        new_en = not bool(b["enabled"])
        await queries.update_bot_enabled(db, bid, new_en)
        b2 = await queries.get_monitored_bot(db, bid)
        assert b2 is not None
        await edit_or_answer(
            cq,
            f"Бот: {b2['display_name']}\nid={bid}\nТокен: {mask_token(str(b2['token']))}\nСтатус: {'вкл' if b2['enabled'] else 'выкл'}",
            reply_markup=kb.bot_detail(bid, bool(b2["enabled"])),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:delask:"))
    async def on_bot_delask(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        b = await queries.get_monitored_bot(db, bid)
        if not b:
            await cq.answer("Не найден", show_alert=True)
            return
        await edit_or_answer(
            cq,
            f"Удалить бота «{b['display_name']}» (id={bid})?",
            reply_markup=kb.confirm_delete(bid),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("bot:del:"))
    async def on_bot_del(cq: CallbackQuery, db: Database) -> None:
        bid = int(cq.data.split(":")[2])
        await queries.delete_monitored_bot(db, bid)
        rows = await queries.list_monitored_bots(db)
        bot_rows = [(int(b["id"]), str(b["display_name"]), bool(b["enabled"])) for b in rows]
        await edit_or_answer(cq, "Бот удалён. Список:", reply_markup=kb.bots_menu(bot_rows))
        await cq.answer()

    @router.callback_query(F.data == "bot:add")
    async def on_bot_add(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AddBotStates.waiting_name)
        await cq.message.answer("Введите отображаемое имя бота (как в списке).")
        await cq.answer()

    @router.message(AddBotStates.waiting_name, F.text)
    async def on_add_name(message: Message, state: FSMContext) -> None:
        name = (message.text or "").strip()
        if not name:
            await message.answer("Имя не может быть пустым.")
            return
        await state.update_data(new_bot_name=name)
        await state.set_state(AddBotStates.waiting_token)
        await message.answer("Отправьте токен бота (из @BotFather). Сообщение можно удалить после добавления.")

    @router.message(AddBotStates.waiting_token, F.text)
    async def on_add_token(message: Message, state: FSMContext, db: Database) -> None:
        token = (message.text or "").strip()
        data = await state.get_data()
        name = str(data.get("new_bot_name") or "").strip()
        if not token or not name:
            await state.clear()
            await message.answer("Сессия сброшена. Начните снова: Боты → + Добавить.")
            return
        timeout = (await queries.load_all_settings(db))["request_timeout_sec"]
        async with httpx.AsyncClient() as client:
            res = await check_getme(client, token, float(timeout))
        if not res.ok:
            await message.answer(
                f"Токен невалиден у Telegram: {res.error_text}. Проверьте токен и попробуйте снова."
            )
            return
        secret = generate_heartbeat_secret()
        new_id = await queries.insert_monitored_bot(db, name, token, secret)
        await state.clear()
        await message.answer(
            f"Бот добавлен: {name} (id={new_id}).\n"
            "Ниже — сниппет, его нужно вставить в код вашего бота рядом с запуском поллинга, "
            "и перезапустить бота. Как только от него придёт первый пинг, в /status появится «ЖИВ».",
            reply_markup=kb.main_menu(),
        )
        snippet = _heartbeat_snippet(secret)
        await message.answer(f"<pre>{snippet}</pre>", parse_mode="HTML")

    @router.message(StateFilter(default_state), F.text, ~F.text.startswith("/"))
    async def fallback_plain(message: Message) -> None:
        await message.answer(
            "Напишите /start — откроется меню.\n"
            "Команды: /status, /failures, /settings"
        )

    register_sites_handlers(router)
    return router


async def _failures_text(db: Database, args: str | None) -> str:
    end = now_moscow_naive().replace(microsecond=0)
    start = (now_moscow_naive() - timedelta(days=7)).replace(microsecond=0)
    if args:
        parts = args.split()
        if len(parts) >= 2:
            try:
                d0 = datetime.strptime(parts[0], "%Y-%m-%d")
                d1 = datetime.strptime(parts[1], "%Y-%m-%d")
                start = d0
                end = d1.replace(hour=23, minute=59, second=59)
            except ValueError:
                return "Формат: /failures или /failures 2026-04-01 2026-04-14"
    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")
    items = await queries.list_all_incidents_in_range(db, start_s, end_s)
    if not items:
        return f"Сбоев за период {start_s} — {end_s} не найдено."
    lines = [f"Инциденты ({start_s} — {end_s}):", ""]
    for it in items:
        ended = it["ended_at"] or "открыт"
        et = it.get("entity_type", "bot")
        eid = it.get("entity_id", "")
        lines.append(
            f"- [{et}] {it['display_name']} (id={eid}): {it['started_at']} → {ended}\n"
            f"  {it['last_error']}"
        )
    return "\n".join(lines)
