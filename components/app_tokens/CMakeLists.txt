# VibePulse som Torget-app: usage + statisk agentmonitor för Claude/Codex.
# app.c, parsrar och monitorlogik delas
# byte-identiskt med simulatorn; nätadaptrarna finns bara i det här bygget.
idf_component_register(
  SRCS "app.c" "tokens_parse.c" "agent_status_parse.c" "agent_usage.c"
       "agent_monitor_policy.c" "agent_monitor.c" "agent_assets.c"
       "agent_net_policy.c" "net.c" "agent_net.c"
  INCLUDE_DIRS "."
  REQUIRES torget_app torget_fmt torget_ticker cjson
  # secrets.h bor i repytroten (gitignorad), därav "../..".
  PRIV_INCLUDE_DIRS "../.."
  PRIV_REQUIRES torget_net esp_http_client)

target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
