from __future__ import annotations

"""Префиксы-эмодзи для Telegram-алертов (единый стиль)."""

_EMOJI_PREFIXES = ("🔴", "⚠️", "✅", "🐢", "☁️", "💾", "📶", "🤖", "🌐", "🌍")


def _with_emoji(emoji: str, text: str) -> str:
    t = text.strip()
    if any(t.startswith(p) for p in _EMOJI_PREFIXES):
        return t
    return f"{emoji} {t}"


def alert_down(text: str) -> str:
    return _with_emoji("🔴", text)


def alert_down_repeat(text: str) -> str:
    return _with_emoji("⚠️", text)


def alert_recover(text: str) -> str:
    return _with_emoji("✅", text)


def alert_slow(text: str) -> str:
    return _with_emoji("🐢", text)


def alert_slow_ok(text: str) -> str:
    return _with_emoji("✅", text)


def alert_telegram_down(text: str) -> str:
    return _with_emoji("☁️", text)


def alert_telegram_recover(text: str) -> str:
    return _with_emoji("☁️✅", text)


def alert_disk(text: str) -> str:
    return _with_emoji("💾", text)


def alert_router_event(text: str, event_type: str) -> str:
    t = text.strip()
    if any(t.startswith(p) for p in _EMOJI_PREFIXES):
        return t
    if event_type == "internet_lte":
        return f"📶🔴 {t}"
    if event_type == "internet_wan":
        return f"📶✅ {t}"
    return f"📶 {t}"
