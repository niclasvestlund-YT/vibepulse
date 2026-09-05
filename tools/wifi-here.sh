#!/bin/sh
# "Panelen, följ med dit jag är." Ett kommando på Macen som ger VibePulse
# nätet Macen redan sitter på — ingen ombyggnad, ingen USB-kabel, ingenting
# att skriva av från glaset.
#
#   1. Panelen reser sin egen accesspunkt (VibePulse-setup). Den gör det
#      själv efter 90 s utan nät, eller om du håller KEY3 ~3 s och väljer
#      WIFI i SETTINGS — hållet öppnar menyn, inte fönstret.
#   2. tools/wifi-here.sh
#
# Skriptet läser Macens nuvarande SSID, hämtar lösenordet ur nyckelringen
# (macOS frågar dig — det är samtycket), hoppar över till panelens
# accesspunkt, lämnar över uppgifterna och släpper tillbaka WiFi:t. Macen är
# offline i ungefär tjugo sekunder.
#
# Panelen MINNS nätet. Nästa gång du är på samma plats ansluter den själv,
# och du kör aldrig det här kommandot igen för just den platsen.
#
# Accesspunktens lösenord härleds ur TG_OTA_TOKEN i secrets.h, samma
# hemlighet som redan får skriva firmware — därför behöver skriptet inget
# som står på skärmen. Saknas token är lösenordet slumpat per fönster och
# står bara på glaset; kör då med:  TG_AP_PASS=<det på skärmen> tools/wifi-here.sh
set -eu
cd "$(dirname "$0")/.."

AP_SSID=VibePulse-setup
AP_HOST=192.168.4.1
DOMAIN=vibepulse-softap-v1

case "$(uname -s)" in
  Darwin) ;;
  *) echo "wifi-here.sh är macOS-specifikt (networksetup + nyckelringen)." >&2
     echo "På annan plattform: anslut till $AP_SSID och öppna http://$AP_HOST/" >&2
     exit 1 ;;
esac

# WiFi-gränssnittet heter inte alltid en0 (Ethernet-dongel, extra portar).
IFACE=$(networksetup -listallhardwareports \
        | awk '/Hardware Port: Wi-Fi/{getline; print $2; exit}')
[ -n "$IFACE" ] || { echo "hittar inget Wi-Fi-gränssnitt" >&2; exit 1; }

# Macens nuvarande nät. networksetup skriver "You are not associated..." när
# WiFi är av — då finns inget nät att ge vidare.
SSID=$(networksetup -getairportnetwork "$IFACE" | sed -n 's/^Current Wi-Fi Network: //p')
[ -n "$SSID" ] || {
  echo "Macen sitter inte på något Wi-Fi-nät — det är nätet vi skulle ge vidare." >&2
  exit 1
}
echo "Macen är på \"$SSID\" ($IFACE)."

# Lösenordet ur systemnyckelringen. Det här är prompten som gör dig till den
# som godkänner överlämningen; skriptet ser aldrig något du inte släppt fram.
# Ett öppet nät har ingen post — tomt lösenord är ett giltigt svar.
PSK=$(security find-generic-password -ga "$SSID" -w \
      /Library/Keychains/System.keychain 2>/dev/null || true)

# Accesspunktens lösenord: härlett ur token, eller övertaget ur miljön.
if [ -n "${TG_AP_PASS:-}" ]; then
  AP_PASS=$TG_AP_PASS
else
  TOKEN=$(sed -n 's/.*TG_OTA_TOKEN[^"]*"\([0-9a-f]\{64\}\)".*/\1/p' secrets.h | head -1)
  [ -n "$TOKEN" ] || {
    echo "inget TG_OTA_TOKEN i secrets.h — läs av lösenordet på glaset och kör:" >&2
    echo "  TG_AP_PASS=<lösenordet> tools/wifi-here.sh" >&2
    exit 1
  }
  AP_PASS=$(printf '%s%s' "$DOMAIN" "$TOKEN" | shasum -a 256 | cut -c1-12)
fi

echo "hoppar över till $AP_SSID (Macen är offline en stund)..."
if ! networksetup -setairportnetwork "$IFACE" "$AP_SSID" "$AP_PASS" >/dev/null 2>&1; then
  echo "kom inte in på $AP_SSID." >&2
  echo "Står WIFI SETUP på glaset? Fönstret är öppet i tio minuter." >&2
  echo "Behöver du ett nytt: håll KEY3 ~3 s och välj WIFI i SETTINGS —" >&2
  echo "hållet öppnar menyn, inte fönstret." >&2
  exit 1
fi

# Tillbaka till Macens eget nät oavsett hur det går härnäst: att lämna
# någon strandad på en panels accesspunkt vore ett uselt slut.
restore() {
  echo "släpper tillbaka Macens WiFi..."
  networksetup -setairportpower "$IFACE" off >/dev/null 2>&1 || true
  sleep 2
  networksetup -setairportpower "$IFACE" on >/dev/null 2>&1 || true
}
trap restore EXIT

# Vänta in DHCP från panelen innan POST:en — direkt efter associationen har
# gränssnittet ännu ingen adress och curl faller på "no route to host".
i=0
while [ "$i" -lt 15 ]; do
  curl -s --max-time 2 "http://$AP_HOST/" >/dev/null 2>&1 && break
  i=$((i + 1))
  sleep 1
done
[ "$i" -lt 15 ] || { echo "panelen svarar inte på http://$AP_HOST/" >&2; exit 1; }

echo "lämnar över \"$SSID\"..."
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
       -X POST "http://$AP_HOST/join" \
       --data-urlencode "ssid=$SSID" \
       --data-urlencode "pass=$PSK")

if [ "$CODE" != "200" ]; then
  echo "panelen avvisade uppgifterna (HTTP $CODE)." >&2
  [ -n "$PSK" ] || echo "Nyckelringen gav inget lösenord — är nätet öppet?" >&2
  exit 1
fi

echo "klart. Panelen ansluter till \"$SSID\" och minns det härnäst."
