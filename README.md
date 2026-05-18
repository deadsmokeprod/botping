# Botping

Сервис на Python: ваши Telegram-боты и устройства в сети сами «пингуют» Botping раз в ~30 секунд. Если пинги пропали — приходит алерт в отдельный **админ-бот**. Настройка списка ботов, роутеров и параметров — прямо в Telegram, без правки кода на сервере.

См. также [SECURITY.md](SECURITY.md) — что не выкладывать в git и что делать при утечке секрета.

---

## Что умеет Botping

| Возможность | Кратко |
|-------------|--------|
| **Heartbeat ботов** | Каждый ваш бот шлёт HTTP на VPS; нет пинга → инцидент |
| **Telegram API** | Раз в минуту `getMe` к admin-боту — отдельный алерт, если API недоступен |
| **MikroTik + LAN** | Роутер пингует IP в локалке и шлёт результат на тот же endpoint |
| **WAN ↔ LTE** | Мгновенное уведомление при переключении канала (не путать с «устройство в LAN недоступно») |
| **Отчёты** | `/report` и опционально ежедневный Excel в 00:00 МСК |

---

## Быстрый старт (≈15 минут)

Нужны: VPS с Linux, аккаунт Telegram, базовые навыки копировать команды в терминал.

### Шаг 1. Админ-бот и ваш chat_id

1. В [@BotFather](https://t.me/BotFather) создайте бота — это **админ-бот** Botping (не путать с ботами, которых вы будете мониторить).
2. Скопируйте **токен** (вид `123456789:AAExampleFakeTokenForDocsOnly` — у вас будет свой).
3. Узнайте свой числовой **chat_id** у [@userinfobot](https://t.me/userinfobot) (пример в документации: `100000001`).

### Шаг 2. Установка на VPS (Docker)

```bash
git clone https://github.com/YOUR_USER/botping.git /opt/botping
cd /opt/botping
cp .env.example .env
```

Откройте `.env` и заполните минимум:

```dotenv
ADMIN_BOT_TOKEN=ваш_токен_от_BotFather
ADMIN_CHAT_IDS=ваш_chat_id
BOTPING_PUBLIC_HOST=198.51.100.42
```

`BOTPING_PUBLIC_HOST` — **публичный IP или домен вашего VPS** (в примере выше — тестовый адрес из RFC 5737; подставьте свой). Без этого сниппеты для ботов покажут заглушку `<IP_VPS>`.

Запуск:

```bash
docker compose up -d --build
```

Или автоматическая установка Docker + первый запуск: `bash deploy/setup-vps.sh`.

Откройте в фаерволе VPS порт **8080/tcp** (heartbeat). База данных — в Docker-volume `botping_data`.

### Шаг 3. Первый вход в админ-бот

Напишите боту **`/start`**. Появится меню: Статус, Боты, Роутеры и устройства, Настройки и т.д.

Доступ только у chat_id из `ADMIN_CHAT_IDS`. После смены `.env` перезапустите контейнер.

### Шаг 4. Подключить мониторимый Telegram-бот

1. **Боты** → **+ Добавить бота** → имя → токен бота из BotFather.
2. Бот пришлёт **сниппет** Python — вставьте его в код **вашего** бота рядом с `start_polling` и перезапустите бота.
3. **Статус** (`/status`) — напротив бота должно быть **ЖИВ** через ~30 с.

Минимальный сниппет (секрет подставит админ-бот):

```python
import asyncio, httpx
BOTPING_URL = "http://198.51.100.42:8080/heartbeat"
HEARTBEAT_SECRET = "demo-heartbeat-secret-32chars!!"

async def _botping_heartbeat():
    headers = {"X-Heartbeat-Secret": HEARTBEAT_SECRET}
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await client.post(BOTPING_URL, headers=headers)
            except Exception:
                pass
            await asyncio.sleep(30)

asyncio.create_task(_botping_heartbeat())  # перед start_polling
```

Секрет можно посмотреть или сменить: **Боты** → карточка бота → «Показать секрет» / «Сменить секрет».

### Шаг 5 (опционально). MikroTik

**Роутеры и устройства** → роутер → IP устройств в LAN → **Установка на MikroTik**. Подробности: [deploy/mikrotik/README.md](deploy/mikrotik/README.md).

---

## Настройка через Telegram

Команды дублируются в **меню «/»** слева от поля ввода.

| Команда / кнопка | Назначение |
|------------------|------------|
| `/start` | Главное меню |
| `/status` или **Статус** | Боты, роутеры, Telegram API, диск |
| `/failures` или **Сбои** | Инциденты за 7 дней; `/failures 2026-04-01 2026-04-14` — за диапазон |
| `/report` или **Отчёт Excel** | Excel за период |
| `/settings` или **Настройки** | Интервалы, таймаут heartbeat, тихие часы, диск и др. |
| **Боты** | Добавить / удалить / сниппет / секрет |
| **Роутеры и устройства** | MikroTik, LAN-цели, WAN/LTE |
| **Диск** | Занятость тома с БД |

Токены и секреты в чате **маскируются**. Полные значения — в SQLite на VPS; защитите файл БД и `.env`.

---

## MikroTik: устройства в LAN

1. **Роутеры и устройства** → **+ Добавить роутер** → имя площадки.
2. Добавьте **устройства** — IP в LAN (например `192.0.2.10` в вашей сети будет свой адрес).
3. **Установка на MikroTik** — скопируйте script + создайте **Scheduler** каждые 30 с.
4. С роутера должен открываться URL Botping (`http://ваш-vps:8080/heartbeat` или `https://botping.example.com/heartbeat`).

Формат тела (заголовок `X-Heartbeat-Secret` как у ботов):

```json
{"checks":[{"id":1,"address":"192.0.2.10","ok":true,"ms":12}]}
```

- Нет пинга от роутера → инцидент «роутер недоступен».
- Пинг до IP не проходит → инцидент по **цели**.

Проверка с VPS или ПК:

```bash
curl -s -X POST "http://198.51.100.42:8080/heartbeat" \
  -H "X-Heartbeat-Secret: demo-heartbeat-secret-32chars!!" \
  -H "Content-Type: application/json" \
  -d '{"checks":[{"address":"192.0.2.10","ok":true,"ms":8}]}'
```

Шаблоны `.rsc`: [deploy/mikrotik/](deploy/mikrotik/).

---

## MikroTik: переключение WAN ↔ LTE

Отдельные **мгновенные** сообщения в Telegram (не ждут таймаута heartbeat и не смешиваются с «камера в LAN недоступна»).

1. **Роутеры и устройства** → роутер → **Переключение WAN/LTE** — два скрипта: `botping-internet-lte` и `botping-internet-wan`.
2. В ваших скриптах failover (`Check_Internet`, `UPLink_WAN`) добавьте одну строку вызова (есть в сниппете бота).
3. Проверка: на роутере **Run Script** → `botping-internet-lte` — сообщение в Telegram и запись в **Журнал переключений**.

Типы событий в JSON:

```json
{"events":[{"type":"internet_lte"}]}
```

```json
{"events":[{"type":"internet_wan"}]}
```

Тихие часы **не блокируют** эти уведомления.

---

## Как работает мониторинг (простыми словами)

- **Бот жив**, если последний heartbeat не старше `heartbeat_timeout_sec` (по умолчанию 120 с). Несколько пропусков подряд (`fail_threshold`, по умолчанию 2) → алерт **НЕДОСТУПЕН**.
- **Восстановление** — сообщение уходит всегда, даже ночью.
- **Тихие часы** — новые и повторные алерты о down подавляются; WAN/LTE и восстановление — нет.
- **Настройки** из Telegram пишутся в БД и подхватываются без перезапуска.
- **Ежедневный Excel** (`daily_excel_report_enabled=1`) — в 00:00 МСК за предыдущие сутки.

---

## Переменные окружения (.env)

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `ADMIN_BOT_TOKEN` | да | Токен админ-бота |
| `ADMIN_CHAT_IDS` | да | chat_id через запятую |
| `DATABASE_PATH` | нет | Путь к SQLite (в Docker: `/app/data/botping.db`) |
| `LOG_LEVEL` | нет | `INFO`, `DEBUG`, … |
| `BOTPING_PUBLIC_HOST` | для сниппетов | IP/домен VPS → `http://host:8080` |
| `BOTPING_PUBLIC_URL` | альтернатива | Полный URL с `https://` если есть TLS |
| `HEARTBEAT_PORT` | нет | Порт heartbeat (по умолчанию 8080) |
| `DEPLOY_SSH_*` | нет | Только локально для [scripts/](scripts/) — **не коммитить** |

Полный шаблон: [.env.example](.env.example).

---

## Бэкап

Скопируйте файл SQLite (`DATABASE_PATH`). Для консистентности лучше остановить контейнер или использовать `.backup` в sqlite3.

---

## Локальная разработка (без Docker)

```bash
git clone https://github.com/YOUR_USER/botping.git
cd botping
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\activate
pip install -e .
cp .env.example .env
# отредактируйте .env
python -m botping.main
```

БД по умолчанию: `./data/botping.db`.

---

## Скрипты деплоя (только ваша машина)

В [scripts/](scripts/) — SSH-обновление VPS (`DEPLOY_SSH_HOST`, `DEPLOY_SSH_PASSWORD` и т.д.). Значения **только в локальном `.env`**, в git не попадают. См. [SECURITY.md](SECURITY.md).

---

<details>
<summary><strong>Технически: почему heartbeat, а не getMe / getUpdates</strong></summary>

- `getMe` проверяет только валидность токена у Telegram — не видит, что процесс бота упал.
- `getUpdates`-зонд ненадёжен: при параллельном polling Telegram может отдать 409 **вашему боту**, а зонду — 200 OK, сбрасывая long-poll.
- Heartbeat подтверждает живость **процесса** бота; сетевые всплески между VPS и Telegram не влияют на per-bot статус так же сильно.

</details>

<details>
<summary><strong>Технически: встроить heartbeat (aiogram, PTB, sync, Node)</strong></summary>

### Требования к клиенту

- `POST` или `GET` на `http(s)://<host>:<port>/heartbeat` каждые ~30 с.
- Заголовок `X-Heartbeat-Secret: <secret>` (или `?secret=`, хуже для логов прокси).
- Ошибки сети не должны ронять бота.
- Старт **до** или параллельно с polling (`asyncio.create_task` / daemon-thread).

Ответ при успехе: `200` и `{"ok": true, "bot_id": N}`.

### Модуль botping_client.py (aiogram 3)

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

Подключение:

```python
asyncio.create_task(heartbeat_loop())
await dp.start_polling(bot)
```

**aiogram 2.x:** `executor.start_polling(dp, on_startup=lambda _: asyncio.create_task(heartbeat_loop()))`.

**python-telegram-bot v20+:** `Application.builder().token(TOKEN).post_init(lambda app: app.create_task(heartbeat_loop())).build()`.

**Синхронный бот:** `threading.Thread(target=..., daemon=True).start()` + `requests.post` в цикле с `time.sleep(30)`.

**Node.js:**

```javascript
setInterval(async () => {
  try {
    await fetch(process.env.BOTPING_URL, {
      method: "POST",
      headers: { "X-Heartbeat-Secret": process.env.BOTPING_SECRET },
    });
  } catch (_) {}
}, 30_000);
```

### Частые ошибки

| Симптом | Решение |
|---------|---------|
| ЖИВ локально, НЕДОСТУПЕН на VPS | URL должен указывать на **публичный** адрес Botping, не `127.0.0.1`; открыт порт 8080 |
| Постоянно 404 | Неверный секрет, секрет &lt; 16 символов, IP в бане — «Сменить секрет», подождать `heartbeat_ban_duration_min` |
| Heartbeat «висит» polling | Используйте фоновую задачу/поток, не блокируйте главный поток |
| Секрет в git | Сменить секрет в боте, обновить env, перезапустить |

`heartbeat_timeout_sec` держите ~в 3–4 раза больше интервала пинга (при 30 с пинге — 90–120 с таймаут).

</details>

<details>
<summary><strong>Технически: безопасность heartbeat</strong></summary>

- По умолчанию HTTP на 8080 + уникальный секрет ≥ 16 символов в `X-Heartbeat-Secret`.
- Лимиты и автобан IP за неверные запросы — в `/settings` (см. подсказки в боте).
- Для продакшна: Caddy/nginx + Let's Encrypt, `BOTPING_PUBLIC_URL=https://botping.example.com`, порт 443.

</details>

<details>
<summary><strong>Технически: HTTPS за reverse proxy (кратко)</strong></summary>

Пример: Caddy с `reverse_proxy localhost:8080`, в `.env` на Botping:

```dotenv
BOTPING_PUBLIC_URL=https://botping.example.com
```

В сниппетах ботов URL станет `https://botping.example.com/heartbeat` без `:8080`.

</details>
