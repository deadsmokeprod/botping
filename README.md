# Botping

Сервис на Python: ваши Telegram-боты, MikroTik и сайты сами «пингуют» Botping на VPS раз в ~30 секунд. Если пинги пропали — алерт в **админ-бот**. Списки объектов, сниппеты установки и параметры мониторинга — в Telegram, без правки кода на сервере.

См. [SECURITY.md](SECURITY.md) — что не выкладывать в git и что делать при утечке секрета.

---

## Что умеет Botping

| Возможность | Кратко |
|-------------|--------|
| **Heartbeat ботов** | Каждый ваш бот шлёт HTTP на VPS; нет пинга → инцидент |
| **Telegram API** | `getMe` раз в 5 мин (настраивается) + backoff при сбоях |
| **MikroTik + LAN** | Роутер пингует IP в локалке и шлёт JSON на `/heartbeat` |
| **WAN ↔ LTE** | Мгновенное уведомление при смене канала (не путать с «устройство в LAN недоступно») |
| **Сайты** | Агент на сервере сайта: DNS + HTTPS, модули (форма, API), push на тот же endpoint |
| **Интерфейс** | Одна панель с inline-кнопками, **◀️ Назад**, `/start` обновляет меню |
| **Наименования** | Подписи в статусе и Excel без смены домена, IP в LAN или секретов |
| **Отчёты** | `/report` и опционально ежедневный Excel в 00:00 МСК |

---

## Быстрый старт (≈15 минут)

Нужны: VPS с Linux, аккаунт Telegram, умение копировать команды в терминал.

### Шаг 1. Админ-бот и ваш chat_id

1. В [@BotFather](https://t.me/BotFather) создайте бота — это **админ-бот** Botping (не путать с ботами, которых вы мониторите).
2. Скопируйте **токен** (пример вида: `123456789:AAExampleFakeTokenForDocsOnly`).
3. Узнайте числовой **chat_id** у [@userinfobot](https://t.me/userinfobot) (пример: `100000001`).

### Шаг 2. Установка на VPS (Docker)

```bash
git clone https://github.com/deadsmokeprod/botping.git /opt/botping
cd /opt/botping
cp .env.example .env
```

Минимум в `.env`:

```dotenv
ADMIN_BOT_TOKEN=ваш_токен_от_BotFather
ADMIN_CHAT_IDS=ваш_chat_id
BOTPING_PUBLIC_HOST=198.51.100.42
```

`BOTPING_PUBLIC_HOST` — **публичный IP или домен VPS** (в примере — тестовый адрес RFC 5737; подставьте свой). Без него сниппеты покажут заглушку `<IP_VPS>`.

```bash
docker compose up -d --build
```

Или: `bash deploy/setup-vps.sh` (Docker + первый запуск).

Откройте порт **8080/tcp** (heartbeat). База — Docker-volume `botping_data`.

### Шаг 3. Первый вход в админ-бот

Напишите **`/start`**. Появится панель: **📊 Статус**, **🤖 Боты**, **🌐 Роутеры**, **🌍 Сайты**, **⚙️ Настройки** и др.

Доступ только у `ADMIN_CHAT_IDS`. После смены `.env` — `docker compose restart`.

### Шаг 4. Подключить Telegram-бот

**🤖 Боты** → **+ Добавить** → имя → токен → вставьте **сниппет** в код бота → перезапуск → **📊 Статус** = **ЖИВ** через ~30 с.

Подробно: [Настройка Telegram-ботов](#настройка-telegram-ботов).

### Шаг 5 (опционально). MikroTik

**🌐 Роутеры** → роутер → устройства в LAN → **Установка MikroTik**.

Подробно: [Настройка MikroTik](#настройка-mikrotik) и [deploy/mikrotik/README.md](deploy/mikrotik/README.md).

### Шаг 6 (опционально). Сайт

**🌍 Сайты** → домен → модули → **🔧 Установка агента** на сервере сайта.

Подробно: [Настройка сайтов](#настройка-сайтов) и [deploy/site-agent/README.md](deploy/site-agent/README.md).

---

## Интерфейс админ-бота

- **Одна панель** — основное меню и разделы открываются в одном сообщении с inline-кнопками (редактируется на месте).
- **Справочные сообщения** — сниппеты Python, секреты, скрипты RouterOS, файл агента сайта приходят **отдельными** сообщениями ниже панели.
- **`/start`** — обновляет панель, удаляет старые panel-сообщения в чате, сбрасывает навигацию.
- **◀️ Назад** — возврат на предыдущий экран (не всегда в главное меню).
- **ℹ️ Справка** — краткая памятка по разделам.

Команды в меню «/» слева от поля ввода:

| Команда | Назначение |
|---------|------------|
| `/start` | 🔄 Обновить интерфейс |
| `/status` | 📊 Статус ботов, роутеров и сайтов |
| `/failures` | ⚠️ Сбои за 7 дней (`/failures 2026-04-01 2026-04-14` — диапазон) |
| `/report` | 📈 Excel за период |
| `/settings` | ⚙️ Настройки мониторинга |

Кнопки главного меню:

| Кнопка | Раздел |
|--------|--------|
| **📊 Статус** | Сводка: боты, роутеры, сайты, Telegram API |
| **⚠️ Сбои** | Инциденты |
| **🤖 Боты** | Добавить / удалить / сниппет / секрет |
| **🌐 Роутеры** | MikroTik, LAN, WAN/LTE, журнал переключений |
| **🌍 Сайты** | Домены, модули, агент |
| **⚙️ Настройки** | Интервалы, тихие часы, диск, **🏷 Наименования** |
| **📈 Excel** | Отчёт за период |
| **💾 Диск** | Занятость тома с БД |

Токены и секреты в справочных сообщениях показываются полностью (это ваш приватный чат). Защитите `.env` и файл SQLite на VPS.

---

## Настройка Telegram-ботов

**Чеклист:** BotFather-токен → добавить в **🤖 Боты** → сниппет в код → перезапуск → **ЖИВ** в статусе.

### Пошагово

1. **🤖 Боты** → **+ Добавить** → отображаемое имя → токен мониторимого бота из BotFather.
2. Бот пришлёт **сниппет** Python — вставьте в код **вашего** бота **до** `start_polling` / `run_polling` и перезапустите процесс.
3. **📊 Статус** — напротив бота **ЖИВ** через ~30 с.
4. Секрет: карточка бота → «Показать секрет» / «Сменить секрет» (после смены обновите переменную в коде бота).

Минимальный сниппет (URL и секрет подставляет админ-бот):

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

Зависимость: `pip install httpx`.

<details>
<summary><strong>Подробнее: aiogram, PTB, sync, Node</strong></summary>

### Требования

- `POST` на `http(s)://<host>:<port>/heartbeat` каждые ~30 с.
- Заголовок `X-Heartbeat-Secret: <secret>` (≥ 16 символов).
- Ошибки сети не должны ронять бота.
- Фоновая задача / daemon-thread, не блокировать polling.

Ответ: `200` и `{"ok": true, "bot_id": N}` (для ботов) или `{"ok": true}` (роутер/сайт).

### aiogram 3

```python
async def heartbeat_loop() -> None:
    headers = {"X-Heartbeat-Secret": BOTPING_SECRET}
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await client.post(BOTPING_URL, headers=headers)
            except Exception:
                pass
            await asyncio.sleep(30)

asyncio.create_task(heartbeat_loop())
await dp.start_polling(bot)
```

**aiogram 2.x:** `on_startup=lambda _: asyncio.create_task(heartbeat_loop())`.

**python-telegram-bot v20+:** `.post_init(lambda app: app.create_task(heartbeat_loop()))`.

**Синхронный бот:** `threading.Thread(..., daemon=True)` + `requests.post` + `time.sleep(30)`.

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
| ЖИВ локально, НЕДОСТУПЕН на VPS | URL на **публичный** адрес Botping; открыт порт 8080 |
| Постоянно 404 | Неверный секрет, IP в бане — сменить секрет, подождать `heartbeat_ban_duration_min` |
| Heartbeat блокирует polling | Только фоновая задача / поток |
| Секрет в git | Сменить секрет, обновить env, перезапустить |

`heartbeat_timeout_sec` ≈ в 3–4 раза больше интервала пинга (при 30 с → 90–120 с).

</details>

---

## Настройка MikroTik

**Чеклист:** роутер в **🌐 Роутеры** → IP устройств → script `botping-lan` + Scheduler 30 с → **ЖИВ** в статусе.

### Устройства в LAN

1. **🌐 Роутеры** → **+ Добавить роутер** → имя площадки.
2. В карточке роутера — **+ Устройство** → имя → IP в LAN (пример: `192.0.2.10`).
3. **Установка MikroTik** — скопируйте script, на роутере:
   - **System → Scripts** → `botping-lan`
   - **System → Scheduler** → каждые `00:00:30` → `/system script run botping-lan`
4. С роутера должен открываться URL Botping (`http://198.51.100.42:8080/heartbeat` или `https://botping.example.com/heartbeat`).

Формат тела (`X-Heartbeat-Secret` как у ботов):

```json
{"checks":[{"id":1,"address":"192.0.2.10","ok":true,"ms":12}]}
```

- Нет пинга от роутера → «роутер недоступен».
- `ok: false` по цели → инцидент по **устройству**.

Проверка с VPS:

```bash
curl -s -X POST "http://198.51.100.42:8080/heartbeat" \
  -H "X-Heartbeat-Secret: demo-heartbeat-secret-32chars!!" \
  -H "Content-Type: application/json" \
  -d '{"checks":[{"id":1,"address":"192.0.2.10","ok":true,"ms":8}]}'
```

Шаблоны `.rsc`: [deploy/mikrotik/](deploy/mikrotik/). Подробности: [deploy/mikrotik/README.md](deploy/mikrotik/README.md).

### Переключение WAN ↔ LTE

Отдельные **мгновенные** сообщения (не ждут таймаут heartbeat; **тихие часы не блокируют**).

1. **🌐 Роутеры** → роутер → **WAN/LTE** — скрипты `botping-internet-lte` и `botping-internet-wan`.
2. В скриптах failover (`Check_Internet`, `UPLink_WAN`) — вызов из сниппета бота.
3. Проверка: **Run Script** → `botping-internet-lte` → сообщение в Telegram и **Журнал переключений**.

```json
{"events":[{"type":"internet_lte"}]}
```

```json
{"events":[{"type":"internet_wan"}]}
```

---

## Настройка сайтов

Botping **не ходит** на сайты сам. На сервере сайта крутится **агент** (Python + cron), проверяет домен и модули, шлёт JSON на тот же `/heartbeat`.

**Чеклист:** сайт в **🌍 Сайты** → модули → `botping-site-agent.py` + cron → **ЖИВ** в статусе.

### Пошагово

1. **🌍 Сайты** → **+ Добавить** → имя → домен (`example.com`, без `https://`).
2. **+ Модуль** → имя → опционально URL/path для автоматической GET-проверки.
3. **🔧 Установка агента** — сохраните `botping-site-agent.py` на сервере сайта:

```bash
python3 -m venv /opt/botping/venv
/opt/botping/venv/bin/pip install httpx
chmod +x /opt/botping/botping-site-agent.py
```

4. Cron каждые 30 с:

```cron
* * * * * /opt/botping/venv/bin/python3 /opt/botping/botping-site-agent.py
* * * * * sleep 30; /opt/botping/venv/bin/python3 /opt/botping/botping-site-agent.py
```

5. **📊 Статус** — домен, resolved IP, модули, время последнего пинга.

Формат push:

```json
{
  "site": {
    "host": "example.com",
    "ip": "93.184.216.34",
    "ok": true,
    "ms": 45,
    "error": null
  },
  "checks": [
    {"id": 1, "ok": true, "ms": 120}
  ]
}
```

Заголовок: `X-Heartbeat-Secret: <секрет из карточки сайта>`.

- Нет heartbeat от агента → сайт недоступен.
- `site.ok: false` → проблема с доменом/HTTPS.
- `checks[].ok: false` → проблема с **модулем**.

Подробности: [deploy/site-agent/README.md](deploy/site-agent/README.md).

<details>
<summary><strong>Подробнее: кастомные проверки и systemd</strong></summary>

### Модули с POST / внутренним API

Если при добавлении модуля URL не указан или нужна форма с POST — отредактируйте `check_module_<id>()` в сгенерированном `botping-site-agent.py`.

### systemd вместо cron

```ini
[Unit]
Description=Botping site agent
After=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/botping/venv/bin/python3 /opt/botping/botping-site-agent.py

[Install]
WantedBy=multi-user.target
```

Таймер `OnUnitActiveSec=30` — аналог интервала 30 с.

</details>

---

## Как работает мониторинг

| Объект | «Жив» | Алерт |
|--------|-------|-------|
| **Бот** | heartbeat не старше `heartbeat_timeout_sec` (по умолчанию 120 с) | После `fail_threshold` пропусков (по умолчанию 2) |
| **Роутер** | тот же принцип по heartbeat роутера | + отдельно по каждой LAN-цели |
| **Сайт** | heartbeat агента + `site.ok` | + отдельно по каждому модулю |
| **Telegram API** | `getMe` раз в 5 мин; алерт после 10+ мин сбоя | Краткие обрывы без спама в чат |
| **WAN/LTE** | — | Мгновенно, вне тихих часов |

- **Восстановление** — сообщение всегда, даже ночью.
- **Тихие часы** — новые down-алерты подавляются; WAN/LTE и восстановление — нет.
- **⚙️ Настройки** из Telegram → в БД, без перезапуска контейнера.
- **🏷 Наименования** — меняется только подпись в UI и Excel; домен, IP в LAN, `id` в JSON и секреты не меняются.
- **Ежедневный Excel** (`daily_excel_report_enabled=1`) — 00:00 МСК за предыдущие сутки.

---

## Переменные окружения (.env)

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `ADMIN_BOT_TOKEN` | да | Токен админ-бота |
| `ADMIN_CHAT_IDS` | да | chat_id через запятую |
| `DATABASE_PATH` | нет | SQLite (Docker: `/app/data/botping.db`) |
| `LOG_LEVEL` | нет | `INFO`, `DEBUG`, … |
| `BOTPING_PUBLIC_HOST` | для сниппетов | IP/домен → `http://host:8080` |
| `BOTPING_PUBLIC_URL` | альтернатива | Полный URL с `https://` |
| `HEARTBEAT_PORT` | нет | Порт heartbeat (8080) |
| `DEPLOY_SSH_HOST` | нет | SSH VPS (только локально) |
| `DEPLOY_SSH_USER` | нет | Пользователь SSH |
| `DEPLOY_SSH_PASSWORD` | нет | Пароль SSH — **не коммитить** |
| `DEPLOY_SSH_PORT` | нет | Порт SSH (22) |
| `DEPLOY_PATH` | нет | Путь на VPS (`/opt/botping`) |

Шаблон: [.env.example](.env.example).

---

## Обновление и деплой

### На VPS вручную

```bash
cd /opt/botping
git pull
docker compose up -d --build
```

Миграции SQLite применяются при старте контейнера автоматически.

### С вашего ПК (SSH + бэкап БД)

В локальном `.env` задайте `DEPLOY_SSH_*` и `DEPLOY_PATH`. После `git push`:

```powershell
cd d:\BOTS\Botping
python scripts/ssh_backup_and_deploy.py
python scripts/ssh_verify_deploy.py
```

`ssh_backup_and_deploy.py` — копия БД в `backups/`, на сервере `git pull` и rebuild.  
`ssh_verify_deploy.py` — проверка контейнера и таблиц.

Скрипты: [scripts/](scripts/). См. [SECURITY.md](SECURITY.md).

---

## Бэкап

Скопируйте SQLite (`DATABASE_PATH`). Для консистентности — остановить контейнер или `sqlite3 .backup`.

---

## Локальная разработка

```bash
git clone https://github.com/deadsmokeprod/botping.git
cd botping
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\activate
pip install -e .
cp .env.example .env
python -m botping.main
```

БД по умолчанию: `./data/botping.db`. Тесты: `python -m pytest tests/ -q`.

---

<details>
<summary><strong>Технически: почему heartbeat, а не getMe / getUpdates</strong></summary>

- `getMe` проверяет только токен у Telegram — не видит падение процесса бота.
- `getUpdates`-зонд ненадёжен: при параллельном polling возможен 409 у бота и 200 у зонда.
- Heartbeat подтверждает живость **процесса**; для роутеров и сайтов — доступность с их стороны сети.

</details>

<details>
<summary><strong>Технически: безопасность heartbeat</strong></summary>

- HTTP на 8080 + уникальный секрет ≥ 16 символов в `X-Heartbeat-Secret`.
- Лимиты и автобан IP — в **⚙️ Настройки** (подсказки в боте).
- Продакшн: Caddy/nginx + Let's Encrypt, `BOTPING_PUBLIC_URL=https://botping.example.com`.

</details>

<details>
<summary><strong>Технически: HTTPS за reverse proxy</strong></summary>

Caddy: `reverse_proxy localhost:8080`. В `.env` на Botping:

```dotenv
BOTPING_PUBLIC_URL=https://botping.example.com
```

Сниппеты получат `https://botping.example.com/heartbeat` без `:8080`.

</details>
