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

-- MikroTik / LAN (push heartbeat + JSON checks)
CREATE TABLE IF NOT EXISTS monitored_routers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_secret TEXT NOT NULL UNIQUE,
    last_heartbeat_at TEXT,
    last_heartbeat_ip TEXT
);

CREATE TABLE IF NOT EXISTS router_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER NOT NULL REFERENCES monitored_routers(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    address TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_ok_at TEXT,
    last_latency_ms INTEGER,
    last_error TEXT,
    UNIQUE(router_id, address)
);

CREATE INDEX IF NOT EXISTS idx_router_targets_router ON router_targets(router_id);

CREATE TABLE IF NOT EXISTS router_target_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES router_targets(id) ON DELETE CASCADE,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    error_text TEXT,
    check_type TEXT NOT NULL DEFAULT 'lan_push'
);

CREATE INDEX IF NOT EXISTS idx_router_target_checks_target_ts ON router_target_checks(target_id, ts);

CREATE TABLE IF NOT EXISTS router_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER NOT NULL REFERENCES monitored_routers(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    last_error TEXT,
    last_alert_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_router_incidents_open ON router_incidents(router_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS router_target_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES router_targets(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    last_error TEXT,
    last_alert_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_router_target_incidents_open ON router_target_incidents(target_id) WHERE ended_at IS NULL;

-- События с роутера (переключение WAN/LTE, произвольный текст)
CREATE TABLE IF NOT EXISTS router_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER NOT NULL REFERENCES monitored_routers(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_router_events_router_ts ON router_events(router_id, created_at);

-- Websites (push heartbeat from site agent)
CREATE TABLE IF NOT EXISTS monitored_websites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    host TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_secret TEXT NOT NULL UNIQUE,
    last_heartbeat_at TEXT,
    last_heartbeat_ip TEXT,
    last_resolved_ip TEXT,
    last_site_ok_at TEXT,
    last_site_latency_ms INTEGER,
    last_site_error TEXT,
    UNIQUE(host)
);

CREATE TABLE IF NOT EXISTS website_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id INTEGER NOT NULL REFERENCES monitored_websites(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    check_hint TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_ok_at TEXT,
    last_latency_ms INTEGER,
    last_error TEXT,
    UNIQUE(website_id, display_name)
);

CREATE INDEX IF NOT EXISTS idx_website_modules_website ON website_modules(website_id);

CREATE TABLE IF NOT EXISTS website_module_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL REFERENCES website_modules(id) ON DELETE CASCADE,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    error_text TEXT,
    check_type TEXT NOT NULL DEFAULT 'module_push'
);

CREATE INDEX IF NOT EXISTS idx_website_module_checks_module_ts ON website_module_checks(module_id, ts);

CREATE TABLE IF NOT EXISTS website_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id INTEGER NOT NULL REFERENCES monitored_websites(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    last_error TEXT,
    last_alert_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_website_incidents_open ON website_incidents(website_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS website_module_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL REFERENCES website_modules(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    last_error TEXT,
    last_alert_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_website_module_incidents_open ON website_module_incidents(module_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS settings_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- приложение пишет Europe/Moscow
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    admin_chat_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);

-- Состояние UI-панели админ-чата (id сообщений с inline-меню)
CREATE TABLE IF NOT EXISTS admin_chat_ui (
    chat_id INTEGER PRIMARY KEY,
    panel_message_id INTEGER,
    panel_ids TEXT NOT NULL DEFAULT '[]'
);
