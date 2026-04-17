# Botping

Сервис на Python для мониторинга ваших Telegram-ботов по модели **heartbeat**: каждый ваш бот раз в ~30 секунд сам шлёт короткий HTTP-запрос на VPS Botping. Если пинги перестали приходить — открывается инцидент и в отдельный админ-бот уходит алерт в указанные `chat_id`. Дополнительно раз в тик Botping проверяет доступность самого Telegram API (`getMe` к токену админ-бота). Все параметры и список ботов настраиваются прямо в Telegram: «Боты» / «Настройки».

## Почему heartbeat, а не getMe/getUpdates

- `getMe` проверяет только валидность токена у Telegram — не видит, что ваш бот-процесс лежит.
- `getUpdates`-зонд ненадёжен: Telegram при параллельном запросе отдаёт 409 **вашему боту**, а нам — 200 OK, причём каждую проверку сбрасывает long-poll. То есть зонд одновременно даёт ложные FAIL и мешает работе бота.
- Heartbeat решает обе проблемы: «живость» подтверждает сам процесс бота, внешние сетевые всплески между VPS и Telegram не влияют на per-bot статус.

## Требования

- Python 3.11+
- Токен админ-бота от [@BotFather](https://t.me/BotFather)
- Ваш числовой `chat_id` (можно узнать у [@userinfobot](https://t.me/userinfobot))
- Открытый входящий TCP-порт `8080` на VPS (значение по умолчанию, меняется в настройках).

## Установка локально

```powershell
cd d:\BOTS\Botping
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
copy .env.example .env
# отредактируйте .env
python -m botping.main
```

Файл БД по умолчанию: `./data/botping.db` (каталог создаётся автоматически).

## Docker (так и крутится на VPS)

```bash
cp .env.example .env
# заполните ADMIN_BOT_TOKEN и ADMIN_CHAT_IDS
docker compose up -d --build
```

Порт `8080` проброшен в [docker-compose.yml](docker-compose.yml). База — в volume `botping_data`.

## Подключение бота (самое важное)

1. В Telegram откройте админ-бота → **Боты** → **+ Добавить бота** → имя → токен. После добавления админ-бот пришлёт готовый **сниппет** с уникальным секретом.
2. В `.env` на VPS укажите публичный адрес Botping, чтобы сниппет подставлял правильный URL:

   ```dotenv
   # один из:
   BOTPING_PUBLIC_HOST=198.51.100.42   # IP VPS (HTTP)
   # или:
   BOTPING_PUBLIC_URL=https://botping.example.com   # если настроили HTTPS через Caddy/nginx
   HEARTBEAT_PORT=8080
   ```
3. Вставьте сниппет в **код вашего бота** рядом с запуском `dp.start_polling(bot)` и перезапустите бота. Как только придёт первый пинг — в `/status` появится «ЖИВ».

Пример сниппета (его же показывает админ-бот, уже с подставленным секретом):

```python
import asyncio, httpx
BOTPING_URL = "http://<IP_VPS>:8080/heartbeat"
HEARTBEAT_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxx"

async def _botping_heartbeat():
    headers = {"X-Heartbeat-Secret": HEARTBEAT_SECRET}
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await client.post(BOTPING_URL, headers=headers)
            except Exception:
                pass
            await asyncio.sleep(30)

# перед dp.start_polling(bot):
asyncio.create_task(_botping_heartbeat())
```

Для существующих ботов секрет можно посмотреть или перевыпустить в карточке бота: «Боты» → выбрать бота → «Показать секрет» / «Сменить секрет».

## Команды и меню в Telegram

Доступ только для `ADMIN_CHAT_IDS`.

- `/start` — главное меню
- `/status` — состояние каждого бота (ЖИВ/НЕДОСТУПЕН), Telegram API и диск
- `/failures` — инциденты за последние 7 дней; `/failures 2026-04-01 2026-04-14` — за диапазон дат
- `/report` — Excel-отчёт за период, человеко-читаемые статусы и длительности
- `/settings` — параметры: интервал оценки, порог пропусков подряд, повтор алерта, **таймаут heartbeat**, **проверка Telegram API (0/1)**, ежедневный Excel (0/1), тихие часы JSON, диск

Токены и секреты в ответах маскируются. Полные значения хранятся в SQLite — защитите файл БД.

## Поведение мониторинга

- **Основная проверка.** Бот считается живым, если последний heartbeat пришёл не позже `heartbeat_timeout_sec` секунд назад (по умолчанию 120 с). Подряд пропусков больше `fail_threshold` (по умолчанию 2) — открывается инцидент с алертом в Telegram.
- **Telegram API-проверка.** Раз в тик делается `getMe` к токену admin-бота. Отдельный инцидент «Telegram API недоступен» открывается/закрывается независимо от per-bot инцидентов. Выключается параметром `telegram_api_probe_enabled=0`.
- **Ежедневный Excel** (`daily_excel_report_enabled=1`): каждую полночь по Москве во все чаты из `ADMIN_CHAT_IDS` уходит тот же Excel, что и по `/report`, за **предыдущие сутки**. Если процесс долго не работал — при следующем запуске догонка по одному файлу на пропущенный день.
- **Тихие часы**: новые и повторные алерты при down подавляются; сообщение о **восстановлении** всегда отправляется.
- Настройки читаются из БД на каждом цикле — изменения из Telegram применяются без перезапуска.

## Безопасность heartbeat

- По умолчанию — HTTP на порт 8080 + случайный секрет на каждого бота в заголовке `X-Heartbeat-Secret`. Этого достаточно, чтобы чужие не засоряли вашу базу.
- Для продакшна рекомендуется поверх поднять Caddy/nginx с Let’s Encrypt: тогда трафик зашифрован, а `BOTPING_PUBLIC_URL` станет `https://…`.

## Бэкап

Достаточно копировать файл SQLite (остановите сервис или используйте `.backup` в sqlite3 для консистентности).
