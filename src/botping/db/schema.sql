PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS monitored_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    token TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    -- Рекомендуется задавать created_at из приложения (Europe/Moscow).
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Персональный секрет для heartbeat-эндпоинта. Бот шлёт его
    -- в заголовке X-Heartbeat-Secret на POST /heartbeat.
    heartbeat_secret TEXT,
    -- Время последнего принятого пинга (Москва) и IP источника.
    last_heartbeat_at TEXT,
    last_heartbeat_ip TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL REFERENCES monitored_bots(id) ON DELETE CASCADE,
    -- приложение пишет метку Europe/Moscow
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    http_status INTEGER,
    error_text TEXT,
    rate_limited INTEGER NOT NULL DEFAULT 0,
    -- тип проверки: 'getupdates' (основной зонд на живость бота)
    -- или 'getme' (исторические записи до миграции)
    check_type TEXT NOT NULL DEFAULT 'getupdates'
);

CREATE INDEX IF NOT EXISTS idx_checks_bot_ts ON checks(bot_id, ts);

-- Глобальная проверка доступности самого Telegram Bot API (вторичная, одна на тик).
CREATE TABLE IF NOT EXISTS telegram_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- приложение пишет метку Europe/Moscow
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    http_status INTEGER,
    error_text TEXT,
    rate_limited INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_telegram_checks_ts ON telegram_checks(ts);

CREATE TABLE IF NOT EXISTS telegram_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- приложение задаёт Europe/Moscow
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    last_error TEXT,
    last_alert_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_telegram_incidents_open ON telegram_incidents(started_at) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL REFERENCES monitored_bots(id) ON DELETE CASCADE,
    -- приложение задаёт Europe/Moscow
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    last_error TEXT,
    last_alert_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_bot_started ON incidents(bot_id, started_at);
CREATE INDEX IF NOT EXISTS idx_incidents_open ON incidents(bot_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS settings_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- приложение пишет Europe/Moscow
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    admin_chat_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);
