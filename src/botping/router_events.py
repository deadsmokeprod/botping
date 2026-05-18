from __future__ import annotations

"""События с MikroTik: шаблоны сообщений и подписи для UI."""

ALLOWED_EVENT_TYPES = frozenset({"internet_lte", "internet_wan", "custom"})

ROUTER_EVENT_DEDUP_SEC = 120

MAX_CUSTOM_TEXT_LEN = 200


def format_router_event_message(
    router_name: str,
    event_type: str,
    *,
    custom_text: str | None = None,
) -> str:
    name = router_name.strip() or "Роутер"
    if event_type == "internet_lte":
        return (
            f"«{name}»: интернет переключён на резерв (LTE). "
            "Основной канал (WAN) недоступен."
        )
    if event_type == "internet_wan":
        return (
            f"«{name}»: снова работает основной интернет (WAN). "
            "Резерв LTE отключён."
        )
    if event_type == "custom":
        text = (custom_text or "").strip() or "событие с роутера"
        return f"«{name}»: {text[:MAX_CUSTOM_TEXT_LEN]}"
    return f"«{name}»: событие ({event_type})"


def internet_channel_label(event_type: str | None) -> str | None:
    """Подпись канала по последнему событию WAN/LTE."""
    if event_type == "internet_lte":
        return "резерв (LTE)"
    if event_type == "internet_wan":
        return "основной (WAN)"
    return None
