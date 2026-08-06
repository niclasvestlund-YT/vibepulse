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
