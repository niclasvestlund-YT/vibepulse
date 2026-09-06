#!/bin/sh
# Skjut en byggd Torget-avbild över luften. Arbetsflödet (2026-08-14):
#
#   1. idf.py build                       (eller -B <byggkatalog>)
#   2. tools/ota-flash.sh <enhetens-ip>   — skriptet väntar på fönstret
#   3. Håll KEY3 ~3 s och välj UPDATE i SETTINGS — hållet öppnar MENYN,
#      inte fönstret. När UPDATES ON-ringen syns går uppladdningen av sig
#      själv, enheten verifierar SHA-256, byter lucka och startar om.
#      (Ligger UPDATE READY-övertagandet redan på glaset: svara med dess
#      UPDATE-pill. Ett håll gör ingenting där.)
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

echo "väntar på underhållsfönstret på $HOST"
echo "  håll KEY3 ~3 s och välj UPDATE i SETTINGS — hållet öppnar bara menyn"
echo "  ligger UPDATE READY redan på glaset: tryck på dess UPDATE-pill."
echo "  ett håll gör ingenting där, så vänta inte på menyn."
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

# CI-BRYGGAN (beställd 2026-08-14, samma kväll som spökbinären): bara
# byggen vars commit har GRÖN CI får gå till glaset. Hashen tas ur
# versionssträngen (gXXXXXXX); en taggad version löses via git. Kräver
# nät + gh — offline-nödfall övermanas med TG_OTA_ALLOW_NO_CI=1.
if [ "${TG_OTA_ALLOW_NO_CI:-0}" != "1" ]; then
  case "$BIN_VERSION" in
    *-g*) BIN_COMMIT=${BIN_VERSION##*-g} ;;
    *)    BIN_COMMIT=$(git rev-parse --short "$BIN_VERSION" 2>/dev/null || true) ;;
  esac
  # gh:s --commit kräver FULL sha (kort hash ger tyst tom lista — mätt
  # 2026-08-14); rev-parse expanderar och bevisar samtidigt att committen
  # finns i det här trädet — en främmande binär vägras på köpet.
  BIN_COMMIT=$(git rev-parse "${BIN_COMMIT:-INGEN}" 2>/dev/null || true)
  if [ -z "${BIN_COMMIT:-}" ]; then
    echo "VÄGRAR: kan inte utläsa commit ur '$BIN_VERSION' — CI går inte att bevisa." >&2
    echo "        TG_OTA_ALLOW_NO_CI=1 om du menar det." >&2
    exit 1
  fi
  CI_GREEN=$(gh run list --commit "$BIN_COMMIT" --status success     --json databaseId --jq length 2>/dev/null || echo X)
  if [ "$CI_GREEN" = "X" ]; then
    echo "VÄGRAR: kan inte fråga GitHub om CI för $BIN_COMMIT (offline? gh?)." >&2
    echo "        TG_OTA_ALLOW_NO_CI=1 om du menar det." >&2
    exit 1
  fi
  if [ "${CI_GREEN:-0}" -lt 1 ]; then
    CI_PENDING=$(gh run list --commit "$BIN_COMMIT" --json status       --jq '[.[]|select(.status!="completed")]|length' 2>/dev/null || echo 0)
    if [ "${CI_PENDING:-0}" -ge 1 ]; then
      echo "VÄGRAR: CI för $BIN_COMMIT kör fortfarande — vänta på grönt." >&2
    else
      echo "VÄGRAR: ingen grön CI för $BIN_COMMIT (opushad? röd?)." >&2
    fi
    echo "        TG_OTA_ALLOW_NO_CI=1 om du menar det." >&2
    exit 1
  fi
  echo "CI grön för $BIN_COMMIT ($CI_GREEN körning(ar))"
fi

echo "fönstret öppet — laddar upp $BIN ($(wc -c < "$BIN" | tr -d ' ') byte):"
CURL_RC=0
RESPONSE=$(curl -sS --max-time 300 -X POST "http://$HOST/api/ota/firmware" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-VibePulse-Project: torget" \
  -H "X-VibePulse-Chip: esp32s3" \
  -H "X-VibePulse-SHA256: $SHA" \
  --data-binary "@$BIN" \
  -w "\n%{http_code} %{time_total}") || CURL_RC=$?

# SVARSGRINDEN: curl sätter INTE exitkod på HTTP-fel utan --fail, så
# statusen MÅSTE läsas ur svaret. Utan den här kontrollen skrev raden
# nedan "vald för nästa boot" också när enheten svarat 400/401/403/408/
# 413/500/503 — operatören som stod vid panelen fick höra att flashen
# gick igenom medan luckan låg orörd och det gamla bygget kördes vidare.
# Enhetens handler (components/torget_ota/ota_service.c) svarar 202 ENDAST
# efter att hela avbilden är skriven, SHA-256:an stämt och den inaktiva
# luckan pekats ut som nästa boot; varje annan väg där går via reject()
# och lämnar bootvalet orört. Därför: exakt 202, annars stopp.
HTTP_STATUS=$(printf '%s\n' "$RESPONSE" | tail -1)
DEVICE_BODY=$(printf '%s\n' "$RESPONSE" | sed '$d')
HTTP_CODE=${HTTP_STATUS%% *}
HTTP_TIME=${HTTP_STATUS#* }
echo "HTTP $HTTP_CODE på ${HTTP_TIME}s"
if [ -n "$DEVICE_BODY" ]; then
  echo "enhetens svar: $DEVICE_BODY"
fi

if [ "$HTTP_CODE" != "202" ]; then
  case "$HTTP_CODE" in
    ""|000)
      # Utan läst status vet vi inte vad enheten hann göra: den kan ha
      # avvisat på headrarna och stängt medan kroppen fortfarande gick, och
      # den kan ha hunnit välja luckan utan att svaret nådde hit. Säg inte
      # mer än så — ärlighetsinvarianten gäller terminalen också.
      HEADLINE="VÄGRAR: inget svar lästes från enheten (curl-fel $CURL_RC) — flashen är INTE bekräftad."
      REASON="nätet, enheten föll bort, eller så avvisade den och stängde innan kroppen var skickad"
      AFTER="Anta ingenting: läs enhetens version på glaset innan du försöker igen." ;;
    *)
      # Ett LÄST avslag är entydigt: varje sådan väg i enhetens handler
      # går via reject(), som avbryter OTA-skrivningen och aldrig rör
      # bootvalet. Den gamla luckan är kvar, orörd.
      HEADLINE="VÄGRAR: enheten svarade $HTTP_CODE, inte 202 — INGEN avbild är vald för boot."
      AFTER="Enheten kör kvar sitt nuvarande bygge. Felsökningstabell: docs/ota.md."
      case "$HTTP_CODE" in
        400) REASON="enheten förkastade avbilden (fel fil, fel SHA-256, eller bruten ström)" ;;
        401) REASON="token avvisad — TG_OTA_TOKEN i secrets.h ska vara exakt 64 gemena hex, samma som enhetens bygge" ;;
        403) REASON="underhållsfönstret var inte öppet — det stängdes under uppladdningen (tio minuter, eller ett kort KEY3-tryck)" ;;
        408) REASON="enheten tröttnade på att vänta på kroppen" ;;
        413) REASON="avbilden får inte plats i den inaktiva luckan" ;;
        500) REASON="enheten misslyckades internt (flashskrivning, validering eller luckval)" ;;
        503) REASON="en uppladdning pågår redan på enheten" ;;
        *)   REASON="oväntad status — enheten dokumenterar bara 202/400/401/403/408/413/500/503" ;;
      esac ;;
  esac
  echo "$HEADLINE" >&2
  echo "        $REASON" >&2
  echo "        $AFTER" >&2
  exit 1
fi

if [ "$CURL_RC" -ne 0 ]; then
  # 202 är enhetens ord, och det ordet gavs efter att luckan valts: att
  # länken dog strax efter (omstarten kommer 1,5 s efter svaret) ändrar
  # inte utfallet. Men det ska synas att det hände.
  echo "(länken bröts efter svaret, curl-fel $CURL_RC — enheten hann svara 202)"
fi
echo "202 = avbilden vald för nästa boot; enheten startar om inom ett par sekunder."
