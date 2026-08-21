#!/bin/sh
# Hosttesterna för Torgets rena kärnor. Kräver en C-kompilator (clang via
# Xcode CLT, eller gcc) samt Python 3.11+ med PyYAML 6.0.3 och Pillow 12.3.0.
#
# Flaggor:
#   --skip-js  hoppa över Node-lanen (relay + interaction-relay). Enbart för
#              CI:s host-gate-jobb — CI kör dem i egna jobb (Worker-sviten
#              npm-cachad). Lokalt körs hela grinden flagglöst: ./test/run.sh
set -e
cd "$(dirname "$0")"

SKIP_JS=0
for arg in "$@"; do
  case "$arg" in
    --skip-js) SKIP_JS=1 ;;
    *) echo "Okänd flagga: $arg (känd: --skip-js)" >&2; exit 2 ;;
  esac
done

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

# Relay crypto stays an optional runtime dependency, but its security vectors
# are part of the host gate once this repository is being developed/tested.
if ! "$PYTHON_BIN" -c \
  'import cryptography; raise SystemExit(0 if cryptography.__version__ == "49.0.0" else 1)' \
  2>/dev/null
then
  printf '%s\n' \
    'ERROR: De krypterade interaktionstesterna kräver cryptography 49.0.0.' \
    'Kör från Torget-repots rot:' \
    '  .venv/bin/python -m pip install -r requirements-interaction-relay.txt' >&2
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
  ../components/app_tokens/needs_you_send_policy.c \
  test_agent_status.c /tmp/torget-cjson.o \
  -lm \
  -o /tmp/torget-agent-status-test
/tmp/torget-agent-status-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/needs_you_policy.c \
  test_needs_you_policy.c \
  -o /tmp/torget-needs-you-test
/tmp/torget-needs-you-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/needs_you_send_policy.c \
  test_needs_you_send_policy.c \
  -o /tmp/torget-needs-you-send-test
/tmp/torget-needs-you-send-test

"$PYTHON_BIN" test_interaction_relay_vectors.py

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -I../components/app_tokens \
  ../components/app_tokens/interaction_relay_policy.c \
  test_interaction_relay_policy.c \
  -o /tmp/torget-interaction-relay-policy-test
/tmp/torget-interaction-relay-policy-test

# Cross-language wire vector: the portable panel HMAC immediately above and
# the tokenserver verifier must pin the same exact v2 bytes forever.
"$PYTHON_BIN" - <<'PY'
import sys
sys.path.insert(0, "..")
from tools.tokenserver.interactions import sign_answer_v2

actual = sign_answer_v2(
    "a" * 64,
    "codex",
    "ABEiM0RVZneImaq7zN3u_w",
    "df55d0b8c9bcccae1eab3d28b985f696b27422f368358169248a4b797991a38d",
    "approve",
    1787097720,
)
expected = "49357b233c81c9979606a52b94aaab578c18fc95c16d0037949b1018c298bbbf"
assert actual == expected, (actual, expected)
print("OK: panelens och tokenserverns v2-HMAC-vektor är identisk")
PY

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/github_status_parse.c \
  test_github_status.c /tmp/torget-cjson.o \
  -lm \
  -o /tmp/torget-github-status-test
/tmp/torget-github-status-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/project_star_popup_policy.c \
  test_project_star_popup_policy.c \
  -o /tmp/torget-project-star-popup-policy-test
/tmp/torget-project-star-popup-policy-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DTK_GITHUB_SOUND_ENABLED=1 \
  ../components/app_tokens/project_star_chime.c \
  test_project_star_chime.c \
  -o /tmp/torget-project-star-chime-test
/tmp/torget-project-star-chime-test

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

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_net/net_source_policy.c \
  test_net_source_policy.c \
  -lm \
  -o /tmp/torget-net-source-test
/tmp/torget-net-source-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_wifi/wifi_slots.c \
  test_wifi_slots.c \
  -lm \
  -o /tmp/torget-wifi-slots-test
/tmp/torget-wifi-slots-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  test_wifi_signal_state.c \
  -o /tmp/torget-wifi-signal-state-test
/tmp/torget-wifi-signal-state-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_wifi/wifi_form.c \
  ../components/torget_wifi/wifi_slots.c \
  test_wifi_form.c \
  -lm \
  -o /tmp/torget-wifi-form-test
/tmp/torget-wifi-form-test

"$PYTHON_BIN" test_agent_demo_wiring.py
"$PYTHON_BIN" test_agent_net_wiring.py
"$PYTHON_BIN" test_github_wiring.py
"$PYTHON_BIN" test_project_star_assets.py
"$PYTHON_BIN" test_target_tls_memory.py
"$PYTHON_BIN" test_buddy_opt_in.py
"$PYTHON_BIN" test_lvgl_layer_safety.py
"$PYTHON_BIN" test_lvgl_memory_config.py
"$PYTHON_BIN" test_vibepulse_layout_wiring.py
"$PYTHON_BIN" test_preview_ui.py
"$PYTHON_BIN" test_ota_partition.py
"$PYTHON_BIN" test_ota_reopen_wiring.py
"$PYTHON_BIN" test_ota_sender_gates.py
"$PYTHON_BIN" test_wifi_setup_wiring.py
"$PYTHON_BIN" test_relay_boundary.py
"$PYTHON_BIN" test_interaction_relay_boundary.py
"$PYTHON_BIN" test_interaction_relay_docs.py
"$PYTHON_BIN" test_interaction_relay_build.py
"$PYTHON_BIN" test_interaction_relay_net_source.py
"$PYTHON_BIN" test_vibepulse_codex_plugin.py

cd ..
"$PYTHON_BIN" -m unittest tools.agent_assets.test_build_agent_images -v
"$PYTHON_BIN" -m unittest tools.vibepulse_studio.test_design \
  tools.vibepulse_studio.test_server -v
"$PYTHON_BIN" tools/vibepulse_studio/design.py --check
"$PYTHON_BIN" test/test_vibepulse_studio_wiring.py
"$PYTHON_BIN" test/test_vibepulse_visual_landmarks.py
"$PYTHON_BIN" test/test_shared_amoled_skill.py
"$PYTHON_BIN" test/test_token_body_capacity.py
"$PYTHON_BIN" test/test_agent_status_body_capacity.py
"$PYTHON_BIN" -m unittest tools.test_hardware_registry -v
# Tokenservermodulerna listas i test/tokenserver-suite.txt — EN lista,
# delad med CI:s tokenserverjobb (PR #11-läxan: två listor gled isär och
# grön CI dolde en NameError). Vakten: varje test_*.py i tools/tokenserver/
# måste stå i listan, så en ny modul inte tyst kan hamna utanför grinden.
for suite_file in tools/tokenserver/test_*.py; do
  suite_module="tools.tokenserver.$(basename "$suite_file" .py)"
  if ! grep -qxF "$suite_module" test/tokenserver-suite.txt; then
    echo "ERROR: $suite_module saknas i test/tokenserver-suite.txt" >&2
    exit 1
  fi
done
"$PYTHON_BIN" -m unittest \
  $(tr -d '\r' < test/tokenserver-suite.txt | grep -v '^#') -v

# Båda molntjänsterna hålls av Node. CI måste ha Node 22; en lokal
# firmwareutvecklare utan Node får ett ärligt hopp i stället för falskt grönt.
if [ "$SKIP_JS" = 1 ]; then
  echo "OBS: --skip-js — relayernas JS-tester körs i CI:s egna jobb"
elif command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  node --test tools/relay/test.mjs
  (cd tools/interaction-relay && npm ci && npm test && npm run typecheck)
elif [ -n "${CI:-}" ]; then
  echo "ERROR: CI requires Node.js 22 + npm for both relay security services" >&2
  exit 1
else
  echo "OBS: node/npm saknas — relayernas JS-tester hoppas över lokalt (CI kör dem)"
fi
