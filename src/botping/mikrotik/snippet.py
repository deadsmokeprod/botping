from __future__ import annotations

from typing import Any


def _escape_routeros_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _ros7_ping_block(target: dict[str, Any], *, first: bool) -> str:
    tid = int(target["id"])
    addr = _escape_routeros_string(str(target["address"]))
    name = str(target["display_name"])
    comma = "" if first else ':set json ($json . ",")\r\n'
    return f"""# --- {name} ---
{comma}:local ok{tid} true
:local ms{tid} 0
:do {{
  :local pr [/ping {addr} count=3]
  :if ($pr = 0) do={{ :set ok{tid} false }}
  :if ($pr > 0) do={{ :set ms{tid} $pr }}
}} on-error={{ :set ok{tid} false }}
:if ($ok{tid}) do={{
  :set json ($json . "{{\\\"id\\\":{tid},\\\"address\\\":\\\"{addr}\\\",\\\"ok\\\":true,\\\"ms\\\":")
  :set json ($json . $ms{tid})
  :set json ($json . "}}")
}} else={{
  :set json ($json . "{{\\\"id\\\":{tid},\\\"address\\\":\\\"{addr}\\\",\\\"ok\\\":false,\\\"error\\\":\\\"timeout\\\"}}")
}}
"""


def build_routeros_snippet(
    url: str,
    secret: str,
    targets: list[dict[str, Any]],
    *,
    ros_version: int = 7,
) -> str:
    """Генерирует script + scheduler для RouterOS."""
    if ros_version != 7:
        return _build_ros6_snippet(url, secret, targets)
    base_url = url.rstrip("/")
    hb_url = base_url if base_url.endswith("/heartbeat") else f"{base_url}/heartbeat"
    esc_url = _escape_routeros_string(hb_url)
    esc_secret = _escape_routeros_string(secret)
    enabled = [t for t in targets if t.get("enabled", True)]
    if enabled:
        blocks: list[str] = []
        for i, t in enumerate(enabled):
            blocks.append(_ros7_ping_block(t, first=(i == 0)))
        ping_blocks = "\n".join(blocks)
    else:
        ping_blocks = "# Нет целей — добавьте в Botping → Сайты → + Цель"

    script = f"""# Botping LAN monitor (RouterOS 7.x)
# После смены целей обновите script на роутере.

:local botpingUrl "{esc_url}"
:local botpingSecret "{esc_secret}"
:local json "{{\\\"checks\\\":["
{ping_blocks}
:set json ($json . "]}}")
:do {{
  /tool fetch url=$botpingUrl http-method=post \\
    http-header-field=("X-Heartbeat-Secret: " . $botpingSecret) \\
    http-header-field="Content-Type: application/json" \\
    http-data=$json check-certificate=no keep-result=no
}} on-error={{}}
"""
    scheduler = (
        "# Scheduler: name=botping-lan, interval=30s, on-event:\n"
        "# /system script run botping-lan\n"
        "# policy: read,write,policy,test"
    )
    return (
        "=== Script: botping-lan (System → Scripts → +) ===\n\n"
        + script
        + "\n\n=== Scheduler ===\n"
        + scheduler
    )


def _build_ros6_snippet(
    url: str, secret: str, targets: list[dict[str, Any]]
) -> str:
    base_url = url.rstrip("/")
    hb_url = base_url if base_url.endswith("/heartbeat") else f"{base_url}/heartbeat"
    lines = [
        "# Botping LAN (RouterOS 6.x — упрощённо)",
        f"# URL: {hb_url}",
        "# JSON в fetch на ROS6 ограничен — для LAN ping используйте ROS 7.",
        "",
        f':local botpingUrl "{_escape_routeros_string(hb_url)}"',
        f':local botpingSecret "{_escape_routeros_string(secret)}"',
        ":do {",
        "  /tool fetch url=$botpingUrl mode=http method=post \\",
        '    http-header-field=("X-Heartbeat-Secret: " . $botpingSecret) \\',
        "    keep-result=no",
        "} on-error={}",
    ]
    if targets:
        lines.append("# Цели в LAN: обновитесь до ROS7 или см. deploy/mikrotik/botping-lan.rsc")
    return "\n".join(lines)
