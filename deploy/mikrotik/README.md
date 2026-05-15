# MikroTik scripts for Botping

Botping мониторит MikroTik и устройства в LAN через **исходящий** HTTP POST на `/heartbeat`.

## Установка (RouterOS 7)

1. В admin-боте Botping: **Сайты** → добавьте роутер и цели → **Показать сниппет**.
2. На роутере: **System → Scripts** → `+` → имя `botping-lan` → вставьте тело script из сниппета.
3. **System → Scheduler** → `+`:
   - Name: `botping-lan`
   - Interval: `00:00:30`
   - On Event: `/system script run botping-lan`
   - Policy: `read,write,policy,test`
4. Убедитесь, что с роутера доступен URL из `.env` (`BOTPING_PUBLIC_HOST` / `BOTPING_PUBLIC_URL`).

## Файлы

| Файл | Описание |
|------|----------|
| `botping-lan.rsc` | Шаблон с плейсхолдерами (для ручной правки) |
| `botping-lan-ros6.rsc` | Только heartbeat роутера без JSON (ROS 6) |

Плейсхолдеры: `__BOTPING_URL__`, `__HEARTBEAT_SECRET__`, `__TARGET_BLOCKS__`

Актуальный скрипт с вашими IP генерирует admin-бот (тот же код, что `src/botping/mikrotik/snippet.py`).

## Формат JSON

```json
{"checks":[{"id":1,"address":"192.168.88.10","ok":true,"ms":12}]}
```

Секрет — заголовок `X-Heartbeat-Secret`.
