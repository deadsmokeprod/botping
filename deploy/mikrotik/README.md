# MikroTik scripts for Botping

Botping мониторит MikroTik и устройства в LAN через **исходящий** HTTP POST на `/heartbeat`.

## Установка (RouterOS 7)

1. В admin-боте Botping: **🌐 Роутеры** → **+ Добавить роутер** → устройства (IP в LAN) → **Установка MikroTik** — скопируйте сниппет.
2. На роутере: **System → Scripts** → `+` → имя `botping-lan` → вставьте тело script из сниппета.
3. **System → Scheduler** → `+`:
   - Name: `botping-lan`
   - Interval: `00:00:30`
   - On Event: `/system script run botping-lan`
   - Policy: `read,write,policy,test`
4. Убедитесь, что с роутера доступен URL из `.env` на VPS (`BOTPING_PUBLIC_HOST` / `BOTPING_PUBLIC_URL`).

## Файлы

| Файл | Описание |
|------|----------|
| `botping-lan.rsc` | Шаблон с плейсхолдерами (для ручной правки) |
| `botping-lan-ros6.rsc` | Только heartbeat роутера без JSON (ROS 6) |

Плейсхолдеры: `__BOTPING_URL__`, `__HEARTBEAT_SECRET__`, `__TARGET_BLOCKS__`

Актуальный скрипт с вашими IP генерирует admin-бот (тот же код, что `src/botping/mikrotik/snippet.py`).

## Формат JSON (ping устройств в LAN)

```json
{"checks":[{"id":1,"address":"192.0.2.10","ok":true,"ms":12}]}
```

Секрет — заголовок `X-Heartbeat-Secret`.

## Переключение WAN ↔ LTE (отдельные уведомления)

Не путать с мониторингом устройств в LAN: это **мгновенное** сообщение в Telegram, когда роутер ушёл на LTE или вернулся на WAN.

1. В admin-боте: **🌐 Роутеры** → роутер → **WAN/LTE** — скопируйте два скрипта `botping-internet-lte` и `botping-internet-wan`.
2. В скриптах failover (`Check_Internet`, `UPLink_WAN`) добавьте вызов (см. конец сниппета в боте).
3. Проверка: **Run Script** → `botping-internet-lte` — в Telegram и в **Журнал переключений**.

Формат JSON для событий:

```json
{"events":[{"type":"internet_lte"}]}
```

```json
{"events":[{"type":"internet_wan"}]}
```

Свой текст (редко нужен):

```json
{"events":[{"type":"custom","text":"Питание UPS на батарее"}]}
```

Подробнее — раздел «MikroTik» в [README.md](../../README.md) в корне репозитория.
