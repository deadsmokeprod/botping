# Botping — только heartbeat роутера (RouterOS 6.x, без JSON LAN)

:local botpingUrl "__BOTPING_URL__"
:local botpingSecret "__HEARTBEAT_SECRET__"
:do {
  /tool fetch url=$botpingUrl mode=http method=post \
    http-header-field=("X-Heartbeat-Secret: " . $botpingSecret) \
    keep-result=no
} on-error={}
