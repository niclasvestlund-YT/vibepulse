#!/bin/sh
# Installera tokenservern som launchd-agent på macOS, så den överlever
# utloggning och omstart. macOS-motsvarigheten till install-windows-task.ps1.
#
#   tools/tokenserver/install-launchd.sh --publish "https://<brevlådan>/u/<hemlighet>"
#   tools/tokenserver/install-launchd.sh              # utan relä, bara LAN
#   tools/tokenserver/install-launchd.sh --uninstall
#   tools/tokenserver/install-launchd.sh --print      # visa plisten, rör inget
#
# Varför ett skript och inte bara `cp se.torget.tokenserver.plist`: den
# incheckade plisten är en MALL med hårdkodade sökvägar från när projektet
# hette Torget (/Users/niclasvestlund/Torget/...). Kopierar man den rakt av
# till en klon som ligger någon annanstans pekar WorkingDirectory fel, och
# `launchctl kickstart` svarar "Could not find service" — utan att någonstans
# säga varför. Det här skriptet fyller i sökvägarna från sin EGEN plats, så
# de kan aldrig peka på en katalog som inte finns.
#
# Mallen saknar dessutom --publish. En tjänst installerad efter README:ns
# autostart-recept serverar alltså LAN men publicerar ALDRIG till reläet, och
# ingenting felar: brevlådan blir bara gammal. Därför tar det här skriptet
# URL:en som argument, precis som Windows-motsvarigheten.
#
# Relä-URL:en hamnar i plisten i klartext — samma exponeringsnivå som
# secrets.h, och samma val Windows-skriptet redan gör.
set -eu
cd "$(dirname "$0")/../.."
REPO=$(pwd)

LABEL=se.torget.tokenserver
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/torget-tokenserver.log"
PYTHON=/usr/bin/python3
PUBLISH=""
PUBLISH_NAME=""
ACTION=install

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --publish)      PUBLISH=${2:?--publish behöver en URL}; shift 2 ;;
    --publish-name) PUBLISH_NAME=${2:?--publish-name behöver ett namn}; shift 2 ;;
    --python)       PYTHON=${2:?--python behöver en sökväg}; shift 2 ;;
    --uninstall)    ACTION=uninstall; shift ;;
    --print)        ACTION=print; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "okänt argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

UID_NUM=$(id -u)

if [ "$ACTION" = uninstall ]; then
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null ||
    launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "avinstallerad. En process som redan kör påverkas inte:"
  echo "  pkill -f tokenserver.py"
  exit 0
fi

[ -f "$REPO/tools/tokenserver/tokenserver.py" ] || {
  echo "hittar inte $REPO/tools/tokenserver/tokenserver.py" >&2
  exit 1
}

# Ärv relä-URL:en från en redan installerad agent när den inte anges. Att
# tappa den vid en ominstallation vore exakt det tysta bortfallet skriptet
# finns för att undvika: allt ser rätt ut, brevlådan slutar bara fyllas.
inherit() {
  [ -f "$PLIST" ] || return 0
  # Avkodningen är inte pedanteri: värdet skrivs tillbaka genom xml() nedan,
  # så ett ärvt "&amp;" hade blivit "&amp;amp;" vid nästa ominstallation och
  # vandrat ett steg längre bort för varje gång.
  awk -v flag="<string>$1</string>" '
    index($0, flag) { getline; gsub(/^[ \t]*<string>/, ""); gsub(/<\/string>.*$/, ""); print; exit }
  ' "$PLIST" | sed -e 's/&lt;/</g' -e 's/&gt;/>/g' -e 's/&amp;/\&/g'
}
[ -n "$PUBLISH" ] || PUBLISH=$(inherit --publish)
[ -n "$PUBLISH_NAME" ] || PUBLISH_NAME=$(inherit --publish-name)

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

ARGS=$(printf '    <string>%s</string>\n' "$(xml "$PYTHON")" "-u" "tokenserver.py")
if [ -n "$PUBLISH" ]; then
  ARGS="$ARGS
$(printf '    <string>%s</string>\n' "--publish" "$(xml "$PUBLISH")")"
  if [ -n "$PUBLISH_NAME" ]; then
    ARGS="$ARGS
$(printf '    <string>%s</string>\n' "--publish-name" "$(xml "$PUBLISH_NAME")")"
  fi
fi

PLIST_XML=$(cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- GENERERAD av tools/tokenserver/install-launchd.sh. Redigera inte här:
     kör om skriptet, annars skiljer sig den installerade agenten från det
     som går att återskapa. Loggen roteras av servern själv vid start. -->
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
$ARGS
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml "$REPO/tools/tokenserver")</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>$(xml "$LOG")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "$LOG")</string>
</dict>
</plist>
XML
)

if [ "$ACTION" = print ]; then
  printf '%s\n' "$PLIST_XML"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
printf '%s\n' "$PLIST_XML" > "$PLIST"
command -v plutil >/dev/null 2>&1 && plutil -lint "$PLIST" >/dev/null

# bootout först: en agent som redan kör med gamla argument ska inte överleva
# en ominstallation. Felet ignoreras — "fanns inte" är precis vad vi vill.
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null ||
  launchctl load "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo "installerad: $PLIST"
echo "  arbetskatalog: $REPO/tools/tokenserver"
if [ -n "$PUBLISH" ]; then
  echo "  publicerar till reläet: ja"
else
  echo "  publicerar till reläet: NEJ (kör om med --publish <url> om du vill)"
fi
echo "  logg: $LOG"
echo
echo "Härefter räcker det här efter en ändring i tools/tokenserver/:"
echo "  launchctl kickstart -k gui/$UID_NUM/$LABEL"
