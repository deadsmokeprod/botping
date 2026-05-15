# Botping LAN — шаблон RouterOS 7.x
# Замените плейсхолдеры или используйте сниппет из Telegram admin-бота.

:local botpingUrl "__BOTPING_URL__"
:local botpingSecret "__HEARTBEAT_SECRET__"
:local json "{\"checks\":["
__TARGET_BLOCKS__
:set json ($json . "]}")
:do {
  /tool fetch url=$botpingUrl http-method=post \
    http-header-field=("X-Heartbeat-Secret: " . $botpingSecret) \
    http-header-field="Content-Type: application/json" \
    http-data=$json check-certificate=no keep-result=no
} on-error={}
