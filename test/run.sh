#!/bin/sh
# Hosttesterna för Torgets rena kärnor. Kräver clang (Xcode CLT) samt
# Python 3.11+ med PyYAML 6.0.3 och Pillow 12.3.0.
set -e
cd "$(dirname "$0")"

PYTHON_BIN=${PYTHON_BIN:-python3}
if ! "$PYTHON_BIN" -c \
  'import PIL, sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 11) and yaml.__version__ == "6.0.3" and PIL.__version__ == "12.3.0" else 1)' \
  2>/dev/null
then
  printf '%s\n' \
    'ERROR: Hosttesterna kräver Python 3.11+ med PyYAML 6.0.3 och Pillow 12.3.0.' \
    'Kör från Torget-repots rot:' \
    '  python3.12 -m venv .venv' \
    '  . .venv/bin/activate' \
    '  python -m pip install -r requirements-dev.txt' \
    'Se README.md under "Hardware knowledge".' >&2
  exit 1
fi

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_fmt/fmt_sv.c \
  ../components/torget_ticker/ticker.c \
  test_solglance.c \
  -lm \
  -o /tmp/torget-core-test
/tmp/torget-core-test

# cJSON kompilerar med sin egen varningsprofil; -Werror gäller VÅRA filer.
cc -std=c11 -O1 -c ../third_party/cjson/cJSON.c -o /tmp/torget-cjson.o

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_tokens/tokens_parse.c \
  test_tokens.c /tmp/torget-cjson.o \
  -lm \
  -o /tmp/torget-tokens-test
/tmp/torget-tokens-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_tokens/max_tracker_parse.c \
  test_max_tracker_parse.c /tmp/torget-cjson.o \
  -lm \
  -o /tmp/torget-max-tracker-test
/tmp/torget-max-tracker-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/max_tracker_presenter.c \
  test_max_tracker_presenter.c \
  -lm \
  -o /tmp/torget-max-tracker-presenter-test
/tmp/torget-max-tracker-presenter-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/usage_presenter.c \
  test_usage_presenter.c \
  -lm \
  -o /tmp/torget-usage-presenter-test
/tmp/torget-usage-presenter-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_tokens/agent_status_parse.c \
  test_agent_status.c /tmp/torget-cjson.o \
  -lm \
  -o /tmp/torget-agent-status-test
/tmp/torget-agent-status-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/agent_usage.c \
  test_agent_usage.c \
  -lm \
  -o /tmp/torget-agent-usage-test
/tmp/torget-agent-usage-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/agent_monitor_policy.c \
  test_agent_monitor_policy.c \
  -lm \
  -o /tmp/torget-agent-monitor-policy-test
/tmp/torget-agent-monitor-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/agent_monitor_policy.c \
  ../components/app_tokens/usage_live_policy.c \
  test_usage_live_policy.c \
  -lm \
  -o /tmp/torget-usage-live-policy-test
/tmp/torget-usage-live-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/agent_completion_policy.c \
  test_agent_completion_policy.c \
  -lm \
  -o /tmp/torget-agent-completion-policy-test
/tmp/torget-agent-completion-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/agent_net_policy.c \
  test_agent_net_policy.c \
  -lm \
  -o /tmp/torget-agent-net-policy-test
/tmp/torget-agent-net-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_ota/ota_policy.c \
  test_ota_policy.c \
  -lm \
  -o /tmp/torget-ota-policy-test
/tmp/torget-ota-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_ota/button_policy.c \
  test_ota_button_policy.c \
  -lm \
  -o /tmp/torget-ota-button-policy-test
/tmp/torget-ota-button-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_ota/notice_policy.c \
  test_ota_notice_policy.c \
  -lm \
  -o /tmp/torget-ota-notice-policy-test
/tmp/torget-ota-notice-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_ota/boot_health_policy.c \
  test_boot_health_policy.c \
  -lm \
  -o /tmp/torget-boot-health-policy-test
/tmp/torget-boot-health-policy-test

"$PYTHON_BIN" test_agent_demo_wiring.py
"$PYTHON_BIN" test_agent_net_wiring.py
"$PYTHON_BIN" test_target_tls_memory.py
"$PYTHON_BIN" test_buddy_opt_in.py
"$PYTHON_BIN" test_lvgl_layer_safety.py
"$PYTHON_BIN" test_vibepulse_layout_wiring.py
"$PYTHON_BIN" test_preview_ui.py
"$PYTHON_BIN" test_ota_partition.py
"$PYTHON_BIN" test_ota_reopen_wiring.py

cd ..
"$PYTHON_BIN" -m unittest tools.agent_assets.test_build_agent_images -v
"$PYTHON_BIN" -m unittest tools.vibepulse_studio.test_design \
  tools.vibepulse_studio.test_server -v
"$PYTHON_BIN" tools/vibepulse_studio/design.py --check
"$PYTHON_BIN" test/test_vibepulse_studio_wiring.py
"$PYTHON_BIN" test/test_vibepulse_visual_landmarks.py
"$PYTHON_BIN" test/test_shared_amoled_skill.py
"$PYTHON_BIN" test/test_token_body_capacity.py
"$PYTHON_BIN" -m unittest tools.test_hardware_registry -v
"$PYTHON_BIN" -m unittest \
  tools.tokenserver.test_tokenserver \
  tools.tokenserver.test_agent_status \
  tools.tokenserver.test_usage_history \
  tools.tokenserver.test_quota_cache \
  tools.tokenserver.test_max_tracker \
  tools.tokenserver.test_value_meter \
  tools.tokenserver.test_update_prices \
  tools.tokenserver.test_codex_usage -v
