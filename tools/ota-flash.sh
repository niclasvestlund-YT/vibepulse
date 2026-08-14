#!/bin/sh
# Skjut en byggd Torget-avbild över luften. Arbetsflödet (2026-08-14):
#
#   1. idf.py build                       (eller -B <byggkatalog>)
#   2. tools/ota-flash.sh <enhetens-ip>   — skriptet väntar på fönstret
#   3. Håll KEY3 ~3 s tills UPDATES ON-ringen syns — uppladdningen går av
#      sig själv, enheten verifierar SHA-256, byter lucka och startar om.
#   4. Efter omstarten återöppnas fönstret själv (PENDING_VERIFY-boot):
#      nästa bygge i samma arbetspass behöver inget nytt håll. Ett kort
#      KEY3-tryck stänger när passet är klart.
#
# Samtyckeskedjan är avsiktlig: fysiskt håll + token + tidsbegränsat
# fönster. Skriptet kan aldrig öppna fönstret åt dig — det är poängen.
# USB-C förblir räddningsvägen om en avbild inte bootar.
set -eu
cd "$(dirname "$0")/.."

# IP:t kan bo i gitignorade .ota-device (repytroten) — då räcker
# `tools/ota-flash.sh` utan argument, från vilken agent-session som helst.
HOST=${1:-$(cat .ota-device 2>/dev/null || true)}
[ -n "$HOST" ] || {
  echo "usage: tools/ota-flash.sh <device-ip> [build-dir]" >&2
  echo "       (eller skriv enhetens IP i .ota-device i repytroten)" >&2
  exit 1
}
TOKEN=$(sed -n 's/.*TG_OTA_TOKEN[^"]*"\([0-9a-f]\{64\}\)".*/\1/p' secrets.h | head -1)
[ -n "$TOKEN" ] || { echo "inget TG_OTA_TOKEN i secrets.h — uppladdning avstängd" >&2; exit 1; }
BUILD_ARG=${2:-}

echo "väntar på underhållsfönstret på $HOST — håll KEY3 ~3 s..."
while ! curl -s --max-time 2 "http://$HOST/api/ota/status" 2>/dev/null \
    | grep -q '"maintenance_open":true'; do
  sleep 1
done

# Filvalet sker HÄR, i uppladdningsögonblicket: en hårdkodad "build" (och
# ett val vid skriptstart, medan ett bygge ännu inte skrivit sin bin)
# sköt morgonens frysspöke till glaset 2026-08-14. Nyaste build*/torget.bin
# NU vinner, och namnet skrivs ut så avsändaren ser vad som faktiskt går.
BUILD=${BUILD_ARG:-$(ls -t build*/torget.bin 2>/dev/null | head -1 | xargs dirname 2>/dev/null)}
[ -n "$BUILD" ] || { echo "ingen build*/torget.bin — bygg först" >&2; exit 1; }
BIN="$BUILD/torget.bin"
[ -f "$BIN" ] || { echo "hittar inte $BIN — bygg först (idf.py build)" >&2; exit 1; }
SHA=$(shasum -a 256 "$BIN" | cut -d' ' -f1)

# Avsändargrinden (läxan 2026-08-14, då ett arkiverat -dirty-diagnosbygge
# gick till glaset och frös det): versionen läses ur BINÄRENS egen
# appbeskrivning och visas ALLTID; ett -dirty-bygge vägras utan uttryckligt
# TG_OTA_ALLOW_DIRTY=1. Enhetens grindar ser bara "en giltig avbild" —
# att den är RÄTT avbild är avsändarens ansvar, och nu även skriptets.
BIN_VERSION=$(dd if="$BIN" bs=1 skip=48 count=32 2>/dev/null | tr -d '\0')
echo "avbildens version: ${BIN_VERSION:-okänd}"
case "$BIN_VERSION" in
  *-dirty*)
    if [ "${TG_OTA_ALLOW_DIRTY:-0}" != "1" ]; then
      echo "VÄGRAR: $BIN_VERSION är ett -dirty-bygge." >&2
      echo "Committa först, eller kör TG_OTA_ALLOW_DIRTY=1 om du menar det." >&2
      exit 1
    fi
    echo "(-dirty släppt igenom av TG_OTA_ALLOW_DIRTY=1)"
    ;;
esac

echo "fönstret öppet — laddar upp $BIN ($(wc -c < "$BIN" | tr -d ' ') byte):"
curl -s --max-time 300 -X POST "http://$HOST/api/ota/firmware" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-VibePulse-Project: torget" \
  -H "X-VibePulse-Chip: esp32s3" \
  -H "X-VibePulse-SHA256: $SHA" \
  --data-binary "@$BIN" \
  -w "\nHTTP %{http_code} på %{time_total}s\n"
echo "202 = avbilden vald för nästa boot; enheten startar om inom ett par sekunder."
