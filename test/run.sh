#!/bin/sh
# Hosttesterna för Torgets rena kärnor. Kräver bara clang (Xcode CLT) —
# samma filer som targetet bygger, bevisade på Macen innan de rör hyllan.
set -e
cd "$(dirname "$0")"

cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/torget_fmt/fmt_sv.c \
  ../components/torget_ticker/ticker.c \
  test_solglance.c \
  -o /tmp/torget-core-test
/tmp/torget-core-test

# cJSON kompilerar med sin egen varningsprofil; -Werror gäller VÅRA filer.
cc -std=c11 -O1 -c ../third_party/cjson/cJSON.c -o /tmp/torget-cjson.o

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_solelkollen/glance_parse.c \
  test_parse.c /tmp/torget-cjson.o \
  -o /tmp/torget-parse-test
/tmp/torget-parse-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_tokens/tokens_parse.c \
  test_tokens.c /tmp/torget-cjson.o \
  -o /tmp/torget-tokens-test
/tmp/torget-tokens-test

cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_tokens/agent_status_parse.c \
  test_agent_status.c /tmp/torget-cjson.o \
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
  -o /tmp/torget-agent-monitor-policy-test
/tmp/torget-agent-monitor-policy-test
