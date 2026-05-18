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
   BOTPING_PUBLIC_HOST=38.00.000.68   # IP VPS (HTTP)
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

## MikroTik и LAN (push + ping)

Мониторинг роутеров и устройств в локальной сети: MikroTik сам пингует IP и шлёт результат на тот же `/heartbeat`.

1. В admin-боте: **Роутеры и устройства** → **+ Добавить роутер** → устройства (IP в LAN).
2. **Установка на MikroTik** — script + scheduler на 30 с (System → Scripts / Scheduler).
3. Шаблоны в репозитории: [deploy/mikrotik/](deploy/mikrotik/README.md).

Формат тела запроса (заголовок `X-Heartbeat-Secret` как у ботов):

```json
{"checks":[{"id":1,"address":"192.168.88.10","ok":true,"ms":12}]}
```

Цель сопоставляется по `id` из Botping, иначе по `address`. Если роутер не пингует Botping — инцидент по роутеру; если ping до IP не проходит — по цели.

Проверка с curl:

```bash
curl -s -X POST "http://<VPS>:8080/heartbeat" \
  -H "X-Heartbeat-Secret: YOUR_ROUTER_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"checks":[{"address":"192.168.88.10","ok":true,"ms":8}]}'
```

## Бэкап

Достаточно копировать файл SQLite (остановите сервис или используйте `.backup` в sqlite3 для консистентности).

## Как встроить heartbeat в свой бот (подробно)

Этот раздел — для тех, кто поднял Botping у себя и хочет, чтобы **его собственные Telegram-боты** слали сюда пинги. Выше, в разделе «Подключение бота», показан минимальный сниппет — здесь же разобраны готовые примеры под популярные фреймворки, куда именно класть код и как проверить, что всё работает.

### Что должен делать ваш бот

- Раз в ~30 секунд отправлять `POST` (или `GET`) на `http(s)://<BOTPING_HOST>:<HEARTBEAT_PORT>/heartbeat`.
- Передавать свой секрет в заголовке `X-Heartbeat-Secret: <secret>` (альтернатива: query-параметр `?secret=<secret>`, но заголовок предпочтительнее — не попадает в логи прокси).
- Игнорировать ошибки сети/таймауты — heartbeat не должен ронять основную работу бота.
- Стартовать цикл heartbeat **после** инициализации event loop и **до** (или параллельно) старта polling/webhook.

Сервер принимает любой body (и пустой), отвечает `200 OK` с JSON `{"ok": true, "bot_id": <id>}` при успехе. Статусы `404`/`429` — это бан/квота, см. «Безопасность heartbeat».

### Где взять URL и секрет

- **Секрет** выдаёт ваш админ-бот при добавлении бота (или по кнопке «Показать секрет» в карточке). Длина ≥ 16 символов, хранится в Telegram — в код можно и хардкодом, и через переменную окружения.
- **URL** собирается из `BOTPING_PUBLIC_HOST` / `BOTPING_PUBLIC_URL` + `HEARTBEAT_PORT` (см. `.env.example`). Если стоите за Caddy/nginx с TLS — используйте `https://…/heartbeat` без порта.

Рекомендую вынести в `.env` / переменные окружения, чтобы не коммитить секрет в репозиторий:

```dotenv
BOTPING_URL=http://38.00.000.68:8080/heartbeat
BOTPING_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Вариант 1. aiogram 3.x (async)

Создайте рядом с основным файлом бота модуль `botping_client.py`:

```python
import asyncio
import logging
import os

import httpx

log = logging.getLogger(__name__)

BOTPING_URL = os.getenv("BOTPING_URL", "http://127.0.0.1:8080/heartbeat")
BOTPING_SECRET = os.environ["BOTPING_SECRET"]
BOTPING_INTERVAL_SEC = int(os.getenv("BOTPING_INTERVAL_SEC", "30"))


async def heartbeat_loop() -> None:
    headers = {"X-Heartbeat-Secret": BOTPING_SECRET}
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                r = await client.post(BOTPING_URL, headers=headers)
                if r.status_code >= 400:
                    log.warning("botping heartbeat non-2xx: %s", r.status_code)
            except Exception as e:
                log.debug("botping heartbeat error: %s", e)
            await asyncio.sleep(BOTPING_INTERVAL_SEC)
```

И подключите в точке входа, рядом со `start_polling`:

```python
from aiogram import Bot, Dispatcher
from botping_client import heartbeat_loop

async def main() -> None:
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    # ... регистрация роутеров ...
    asyncio.create_task(heartbeat_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

### Вариант 2. aiogram 2.x (async)

```python
from aiogram import Bot, Dispatcher, executor
from botping_client import heartbeat_loop

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

async def on_startup(_):
    import asyncio
    asyncio.create_task(heartbeat_loop())

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
```

### Вариант 3. python-telegram-bot v20+ (async)

PTB сам управляет event loop, поэтому используйте `JobQueue` или фоновую таску через `post_init`:

```python
from telegram.ext import Application
from botping_client import heartbeat_loop

async def _post_init(app: Application) -> None:
    import asyncio
    app.create_task(heartbeat_loop())

app = Application.builder().token(TOKEN).post_init(_post_init).build()
app.run_polling()
```

### Вариант 4. Синхронный бот (pyTelegramBotAPI, старые версии PTB, свои решения)

Если ваш бот синхронный — запускайте heartbeat в отдельном потоке, чтобы он не блокировал polling:

```python
import os
import threading
import time

import requests

BOTPING_URL = os.getenv("BOTPING_URL", "http://127.0.0.1:8080/heartbeat")
BOTPING_SECRET = os.environ["BOTPING_SECRET"]


def _heartbeat_loop() -> None:
    headers = {"X-Heartbeat-Secret": BOTPING_SECRET}
    while True:
        try:
            requests.post(BOTPING_URL, headers=headers, timeout=10)
        except Exception:
            pass
        time.sleep(30)


def start_heartbeat() -> None:
    t = threading.Thread(target=_heartbeat_loop, name="botping-heartbeat", daemon=True)
    t.start()


if __name__ == "__main__":
    start_heartbeat()
    bot.infinity_polling()  # ваш polling
```

### Вариант 5. Node.js / aiogram-подобные боты на других языках

Сервер принимает обычный HTTP — подойдёт любой клиент:

```javascript
const BOTPING_URL = process.env.BOTPING_URL;
const BOTPING_SECRET = process.env.BOTPING_SECRET;

setInterval(async () => {
  try {
    await fetch(BOTPING_URL, {
      method: "POST",
      headers: { "X-Heartbeat-Secret": BOTPING_SECRET },
    });
  } catch (_) { /* ignore */ }
}, 30_000);
```

Curl для отладки:

```bash
curl -i -X POST "$BOTPING_URL" -H "X-Heartbeat-Secret: $BOTPING_SECRET"
# Ожидаем HTTP/1.1 200 OK и тело {"ok":true,"bot_id":N}
```

### Куда именно вставлять

1. Создайте объект бота и диспетчер **как обычно**.
2. Зарегистрируйте роутеры/хендлеры.
3. **Перед** запуском polling/webhook стартуйте heartbeat (`asyncio.create_task(...)` в async-мире или `threading.Thread(..., daemon=True)` в sync).
4. Запускайте polling. Порядок важен: если стартовать heartbeat после блокирующего polling, он никогда не запустится.

### Проверка, что всё работает

1. Запустите ваш бот.
2. В админ-боте откройте `/status` — напротив вашего бота должно появиться «ЖИВ», last_seen обновляется каждые ~30 сек.
3. Остановите бот — спустя `heartbeat_timeout_sec` (по умолчанию 120 с) и `fail_threshold` пропусков подряд (по умолчанию 2) придёт алерт «НЕДОСТУПЕН».
4. Запустите снова — при следующем heartbeat инцидент автоматически закроется сообщением «восстановлен».

### Частые ошибки

- **Бот ЖИВ, пока локально запущен, но на VPS — «НЕДОСТУПЕН».** Проверьте, что в боте URL указывает на **публичный** адрес Botping, а не на `127.0.0.1`. На VPS — что порт `HEARTBEAT_PORT` открыт в фаерволе (`ufw allow 8080/tcp`) и проброшен в `docker-compose.yml`.
- **Всё время 404.** Неверный секрет, секрет короче 16 символов либо IP уже в бане за флуд. Подождите `heartbeat_ban_duration_min` минут, сверьте секрет с «Показать секрет» в админ-боте.
- **В Docker-контейнере heartbeat не достаёт до Botping.** Если Botping и клиентский бот на одной машине — используйте внутренний IP/имя сервиса, а не `127.0.0.1` изнутри контейнера.
- **Heartbeat блокирует бот.** Значит вы вызвали его синхронно в одном потоке с polling. В async — используйте `asyncio.create_task`, в sync — `threading.Thread(..., daemon=True)`.
- **Секрет утёк в git.** Откройте карточку бота → «Сменить секрет», обновите переменную окружения у бота, перезапустите его. Старый секрет сразу перестаёт работать.

### Настройка частоты и таймаутов

- Интервал на стороне бота — 30 секунд, значение по умолчанию. Можно уменьшить до 10–15 с; учтите лимит `heartbeat_unauth_rate_per_min` (300 rpm на IP для авторизованных пингов).
- Серверный таймаут меняется в Telegram: `/settings` → `heartbeat_timeout_sec`. Держите его в ~3–4 раза больше интервала пинга, иначе редкие сетевые провалы будут давать ложные алерты.
- Порог пропусков подряд до открытия инцидента — `fail_threshold` там же.
