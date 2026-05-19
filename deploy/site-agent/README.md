# Агент мониторинга сайта (Botping)

Скрипт на **сервере сайта** проверяет домен (DNS + HTTPS), модули и шлёт JSON на `POST /heartbeat` Botping — как MikroTik для LAN.

## Требования

- Python 3.10+
- `pip install httpx`
- Исходящий доступ до URL Botping (`BOTPING_PUBLIC_HOST` / `BOTPING_PUBLIC_URL` на VPS)

## Установка

1. В Telegram: **🌍 Сайты** → добавить сайт и модули → **🔧 Установка агента**.
2. Сохраните выданный `botping-site-agent.py` на сервере, например `/opt/botping/botping-site-agent.py`.
3. Убедитесь, что в скрипте верные `BOTPING_URL` и `HEARTBEAT_SECRET` (или задайте через env).

```bash
python3 -m venv /opt/botping/venv
/opt/botping/venv/bin/pip install httpx
chmod +x /opt/botping/botping-site-agent.py
```

## Cron (каждые 30 секунд)

```cron
* * * * * /opt/botping/venv/bin/python3 /opt/botping/botping-site-agent.py
* * * * * sleep 30; /opt/botping/venv/bin/python3 /opt/botping/botping-site-agent.py
```

## Формат push

```json
{
  "site": {
    "host": "mongol.pro",
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

Заголовок: `X-Heartbeat-Secret: <секрет из бота>`.

## Модули

- Если при добавлении модуля указан URL/путь — в скрипте будет GET-проверка.
- Для формы с POST или внутренним API — отредактируйте функцию `check_module_<id>()` в сгенерированном файле.

## Проверка

После первого запуска в боте **📊 Статус** и карточка сайта должны показать домен, IP и время последнего пинга.
