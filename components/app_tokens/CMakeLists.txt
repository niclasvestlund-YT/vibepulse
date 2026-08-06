# Tokenmätaren som Torget-app: en tickande Mtok-mätare över Claude Code-
# användningen. app.c, tokens_parse.c och agent_status_parse.c delas
# byte-identiskt med simulatorn; net.c finns bara i det här bygget.
idf_component_register(
  SRCS "app.c" "tokens_parse.c" "agent_status_parse.c" "net.c"
  INCLUDE_DIRS "."
  REQUIRES torget_app torget_fmt torget_ticker cjson
  # secrets.h bor i repytroten (gitignorad), därav "../..".
  PRIV_INCLUDE_DIRS "../.."
  PRIV_REQUIRES torget_net)

target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
