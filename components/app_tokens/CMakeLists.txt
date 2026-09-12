set(TK_APP_TOKEN_SRCS
       "app.c" "labs_features.c" "labs_store.c" "tokens_parse.c" "usage_presenter.c" "usage_screen.c"
       "agent_status_parse.c" "agent_usage.c"
       "agent_monitor_policy.c" "usage_live_policy.c" "agent_completion_policy.c"
       "needs_you_policy.c" "needs_you_send_policy.c"
       "interaction_relay_policy.c"
       "agent_status_source_policy.c"
       "agent_monitor.c" "agent_assets.c"
       "github_status_parse.c" "project_star_popup_policy.c"
       "project_star_assets.c" "project_star_chime.c" "project_star_popup.c"
       "agent_net_policy.c" "tokens_net_recovery_policy.c"
       "poll_backoff_policy.c"
       "net.c" "agent_net.c" "needs_you_net.c"
       "github_net.c"
       "max_tracker_parse.c" "max_tracker_presenter.c")
if(CONFIG_TK_VIBEPULSE_INTERACTION_RELAY OR CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY)
  # Shape contracts (not values): URL=https origin, mailbox
  # vp_[A-Za-z0-9_-]{16}, token [A-Za-z0-9_-]{43}, key [A-Fa-f0-9]{64}.
  set(TK_IR_RECOVERY [=[Encrypted interaction relay is enabled but incomplete.
Add URL, mailbox, panel token, and the 64-hex device key to secrets.h,
or run: python3 tools/vibepulse_setup.py relay doctor
To return to LAN-only: disable TK_VIBEPULSE_INTERACTION_RELAY and
TK_VIBEPULSE_AGENT_STATUS_RELAY in menuconfig.]=])
  set(TK_SECRETS_PATH "${CMAKE_CURRENT_LIST_DIR}/../../secrets.h")
  if(NOT EXISTS "${TK_SECRETS_PATH}")
    message(FATAL_ERROR "${TK_IR_RECOVERY}")
  endif()
  file(READ "${TK_SECRETS_PATH}" TK_SECRETS_TEXT LIMIT 32768)
  foreach(TK_IR_NAME IN ITEMS
      TK_VIBEPULSE_INTERACTION_RELAY_URL
      TK_VIBEPULSE_INTERACTION_MAILBOX
      TK_VIBEPULSE_INTERACTION_PANEL_TOKEN
      TK_VIBEPULSE_DEVICE_KEY)
    string(REGEX MATCH
      "(^|\n)[ \t]*#[ \t]*define[ \t]+${TK_IR_NAME}[ \t]+\"([^\"]*)\""
      TK_IR_${TK_IR_NAME} "${TK_SECRETS_TEXT}")
    if(NOT TK_IR_${TK_IR_NAME})
      message(FATAL_ERROR "${TK_IR_RECOVERY}")
    endif()
    set(TK_IR_VALUE_${TK_IR_NAME} "${CMAKE_MATCH_2}")
  endforeach()
  set(TK_IR_URL "${TK_IR_VALUE_TK_VIBEPULSE_INTERACTION_RELAY_URL}")
  set(TK_IR_MAILBOX "${TK_IR_VALUE_TK_VIBEPULSE_INTERACTION_MAILBOX}")
  set(TK_IR_PANEL_TOKEN "${TK_IR_VALUE_TK_VIBEPULSE_INTERACTION_PANEL_TOKEN}")
  set(TK_IR_DEVICE_KEY "${TK_IR_VALUE_TK_VIBEPULSE_DEVICE_KEY}")
  string(LENGTH "${TK_IR_MAILBOX}" TK_IR_MAILBOX_LEN)
  string(LENGTH "${TK_IR_PANEL_TOKEN}" TK_IR_PANEL_TOKEN_LEN)
  string(LENGTH "${TK_IR_DEVICE_KEY}" TK_IR_DEVICE_KEY_LEN)
  string(LENGTH "${TK_IR_URL}" TK_IR_URL_LEN)
  if(NOT TK_IR_URL MATCHES "^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$" OR
     TK_IR_URL_LEN GREATER 200 OR
     NOT TK_IR_MAILBOX MATCHES "^vp_[A-Za-z0-9_-]+$" OR
     NOT TK_IR_MAILBOX_LEN EQUAL 19 OR
     NOT TK_IR_PANEL_TOKEN MATCHES "^[A-Za-z0-9_-]+$" OR
     NOT TK_IR_PANEL_TOKEN_LEN EQUAL 43 OR
     NOT TK_IR_DEVICE_KEY MATCHES "^[A-Fa-f0-9]+$" OR
     NOT TK_IR_DEVICE_KEY_LEN EQUAL 64)
    message(FATAL_ERROR "${TK_IR_RECOVERY}")
  endif()
  list(APPEND TK_APP_TOKEN_SRCS
       "interaction_relay_crypto.c" "interaction_relay_net.c")
endif()

# VibePulse som Torget-app: usage + statisk agentmonitor för Claude/Codex.
# app.c, parsrar och monitorlogik delas byte-identiskt med simulatorn;
# nätadaptrarna finns bara i det här bygget.
idf_component_register(
  SRCS ${TK_APP_TOKEN_SRCS}
  INCLUDE_DIRS "."
  REQUIRES torget_app torget_fmt cjson
  # secrets.h bor i repytroten (gitignorad), därav "../..".
  PRIV_INCLUDE_DIRS "../.."
  PRIV_REQUIRES torget_net esp_http_client nvs_flash)

# ESP-IDF discovers PRIV_REQUIRES before Kconfig values exist. Add the optional
# relay dependencies to the component target after configuration instead, so a
# relay-off build does not acquire crypto/timer dependencies.
if(CONFIG_TK_VIBEPULSE_INTERACTION_RELAY OR CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY)
  foreach(TK_IR_DEP IN ITEMS mbedtls esp_hw_support esp_timer)
    idf_component_get_property(TK_IR_DEP_LIB ${TK_IR_DEP} COMPONENT_LIB)
    target_link_libraries(${COMPONENT_LIB} PRIVATE ${TK_IR_DEP_LIB})
  endforeach()
endif()

target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
