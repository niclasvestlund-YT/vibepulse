#!/bin/sh
# Bygg och skjut varje ändring över luften, utan att röra tangentbordet.
#
#   tools/ota-watch.sh                 [enhetens-ip] [byggkatalog]
#
# IP:t löser ota-flash.sh själv ur .ota-device; ge det bara som argument om
# du kör mot en annan enhet än den vanliga.
#
# Samtyck EN gång per pass — håll KEY3 ~3 s, eller svara UPDATE på
# takeovern. Därefter sköter sig resten själv: varje sparad källfil bygger
# om, och varje bygge som faktiskt blev en ny avbild laddas upp. Enheten
# startar om, bootar PENDING_VERIFY och återöppnar fönstret åt sig själv
# (ota_service.c), så nästa varv behöver inget nytt samtycke. Ett kort
# KEY3-tryck stänger när passet är slut — då står loopen och väntar.
#
# Varför det här inte kringgår samtyckeskedjan: loopen kan lika lite som
# ota-flash.sh öppna fönstret. Den väntar bara ut det. Det fysiska hållet
# är fortfarande enda sättet att släppa in en avbild, och USB-C är kvar som
# räddningsväg. Skillnaden är att du gör det en gång per pass i stället för
# en gång per bygge.
set -eu
cd "$(dirname "$0")/.."

# Tom sträng är giltigt: ota-flash.sh läser då .ota-device själv, och äger
# felmeddelandet om ingendera finns. En kopia av den regeln här skulle bara
# hinna bli osann.
HOST=${1:-}
BUILD=${2:-build}
BIN="$BUILD/torget.bin"
INTERVAL=${OTA_WATCH_INTERVAL:-2}

command -v idf.py >/dev/null 2>&1 || {
  echo "idf.py saknas — source ~/esp/esp-idf/export.sh först" >&2; exit 127; }

# Samma summa som ota-flash.sh räknar, samma verktyg på båda plattformar.
if command -v shasum >/dev/null 2>&1; then HASH="shasum -a 256"; else HASH="sha256sum"; fi
sha_of() { $HASH "$1" | cut -d' ' -f1; }

# Källträdets fingeravtryck: spårade filer som faktiskt går in i avbilden.
# git ls-files håller listan ärlig — nya filer räknas, byggartefakter aldrig.
#
# Summan går på INNEHÅLL, inte på tidsstämplar: ls -l visar minut, så två
# sparningar inom samma minut med oförändrad storlek hade sett identiska ut
# och tyst hoppats över. Att hasha ett par hundra små källfiler tar
# millisekunder och kan inte missa en ändring.
fingerprint() {
  git ls-files -z main components platform sim CMakeLists.txt sdkconfig.defaults \
      partitions.csv 2>/dev/null \
    | xargs -0 $HASH 2>/dev/null | $HASH
}

printf 'bevakar källträdet, mål %s — samtyck en gång: KEY3 ~3 s eller UPDATE\n' \
  "${HOST:-$(cat .ota-device 2>/dev/null || echo '.ota-device')}"
last_src=""
last_bin=""
[ -f "$BIN" ] && last_bin=$(sha_of "$BIN")

while :; do
  src=$(fingerprint)
  if [ "$src" != "$last_src" ]; then
    last_src=$src
    printf '\n--- ändring upptäckt, bygger ---\n'
    if ! idf.py -B "$BUILD" build; then
      # Ett trasigt bygge är en vanlig mellanstation när man skriver kod.
      # Loopen dör inte av det; den väntar på nästa sparning.
      printf '!!! bygget gick inte igenom — väntar på nästa ändring\n' >&2
      continue
    fi
    bin_sha=$(sha_of "$BIN")
    if [ "$bin_sha" = "$last_bin" ]; then
      printf '=== samma avbild som förra uppladdningen, hoppar över\n'
      continue
    fi
    printf '=== ny avbild %s — laddar upp\n' "$(echo "$bin_sha" | cut -c1-12)"
    if sh tools/ota-flash.sh "$HOST" "$BUILD"; then
      last_bin=$bin_sha
    else
      printf '!!! uppladdningen misslyckades — försöker igen vid nästa ändring\n' >&2
    fi
  fi
  sleep "$INTERVAL"
done
