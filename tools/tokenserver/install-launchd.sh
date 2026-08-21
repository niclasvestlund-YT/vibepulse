#!/bin/sh
# Installera tokenservern som launchd-agent på macOS, så den överlever
# utloggning och omstart. macOS-motsvarigheten till install-windows-task.ps1.
#
#   tools/tokenserver/install-launchd.sh --from-running   # ta över en igång-
#                                                         # varande tjänst
#   tools/tokenserver/install-launchd.sh -- --github-repo ägare/repo \
#       --claude-plan max20x --publish "https://<brevlådan>/u/<hemlighet>"
#   tools/tokenserver/install-launchd.sh --publish "https://..."  # bara relä
#   tools/tokenserver/install-launchd.sh              # ärver befintliga flaggor
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
# ingenting felar: brevlådan blir bara gammal.
#
# ALLA tokenserverflaggor följer med, inte bara --publish. --github-repo,
# --claude-plan, --codex-plan och --plan är CLI-only precis som --publish
# (inget av dem sparas i tjänstens config), så ett skript som bara kunde
# hantera relä-URL:en hade tyst släckt GitHub-sidan, planmärkena och
# värdemultipelns nämnare vid installationen. Argumenten kommer från, i
# fallande ordning: det som står efter "--", --from-running, eller den redan
# installerade agenten. Tomt bara om ingen av dem finns.
#
# Relä-URL:en hamnar i plisten i klartext — samma exponeringsnivå som
# secrets.h, och samma val Windows-skriptet redan gör.
set -eu
cd "$(dirname "$0")/../.."
REPO=$(pwd)

LABEL=se.torget.tokenserver
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/torget-tokenserver.log"
# Den python3 som finns i PATH vid installationen, som absolut sökväg —
# launchd ärver inte skalets PATH, och en tjänst som redan kör på t.ex.
# Homebrews 3.12 ska inte tyst flyttas till systemets äldre /usr/bin/python3.
PYTHON=$(command -v python3 || echo /usr/bin/python3)
PUBLISH=""
PUBLISH_NAME=""
TAIL=""
TAIL_SOURCE=""
ACTION=install

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

FROM_RUNNING=0
while [ $# -gt 0 ]; do
  case "$1" in
    --publish)      PUBLISH=${2:?--publish behöver en URL}; shift 2 ;;
    --publish-name) PUBLISH_NAME=${2:?--publish-name behöver ett namn}; shift 2 ;;
    --python)       PYTHON=${2:?--python behöver en sökväg}; shift 2 ;;
    --from-running) FROM_RUNNING=1; shift ;;
    --uninstall)    ACTION=uninstall; shift ;;
    --print)        ACTION=print; shift ;;
    -h|--help)      usage; exit 0 ;;
    --)             shift
                    # Allt efter -- är tokenserverns egna flaggor, ordagrant.
                    for arg in "$@"; do
                      TAIL="$TAIL$arg
"
                    done
                    TAIL_SOURCE="angivna på kommandoraden"
                    break ;;
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

# Avkodningen är inte pedanteri: värdena skrivs tillbaka genom xml() nedan,
# så ett ärvt "&amp;" hade blivit "&amp;amp;" vid nästa ominstallation och
# vandrat ett steg längre bort för varje gång.
unxml() { sed -e 's/&lt;/</g' -e 's/&gt;/>/g' -e 's/&amp;/\&/g'; }
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# Hela svansen efter tokenserver.py, från den redan installerade agenten.
# Att ärva ALLT och inte flagga för flagga är poängen: en flagga skriptet
# inte känner till får inte kunna försvinna vid en ominstallation.
inherit_tail() {
  [ -f "$PLIST" ] || return 0
  awk '
    /<key>ProgramArguments<\/key>/ { inarr = 1; next }
    inarr && /<\/array>/ { exit }
    inarr && /<string>/ {
      line = $0
      sub(/^[ \t]*<string>/, "", line); sub(/<\/string>.*$/, "", line)
      if (started) print line
      if (line ~ /tokenserver\.py$/) started = 1
    }
  ' "$PLIST" | unxml
}

# Samma svans, men från den tjänst som kör just nu. Vägen in för en tjänst
# som startats för hand med tio flaggor: skriv inte om dem (och läck inte
# relä-hemligheten genom ett terminalfönster), ta dem som de är.
# En kandidat måste STARTA med en pythontolk. "pgrep -f tokenserver.py" ensamt
# matchar allt vars kommandorad NÄMNER filen -- ett skal, en editor, det här
# skriptet körd från en rad som råkar innehålla namnet -- och då hamnade hela
# den kommandoraden i plisten som tokenserverns argument. Verifierat: utan
# den här kontrollen fångade en testkörning sitt eget skal.
looks_like_tokenserver() {
  first=${1%% *}
  case "$first" in
    python|python3|*/python|*/python3|*/Python|*/pythonw) ;;
    *) return 1 ;;
  esac
  printf '%s' "$1" | tr ' ' '\n' | grep -q '^.*tokenserver\.py$'
}

running_tail() {
  found=""
  for pid in $(pgrep -f 'tokenserver\.py' 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    args=$(ps -o args= -p "$pid" 2>/dev/null) || continue
    looks_like_tokenserver "$args" || continue
    found="$found$pid "
  done
  set -- $found
  [ $# -gt 0 ] || { echo "hittar ingen tokenserver.py som kör" >&2; exit 1; }
  [ $# -eq 1 ] || {
    echo "flera tokenserver.py kör ($found) — stoppa alla utom en först" >&2
    exit 1
  }
  RUNNING_PID=$1
  # ps ger argumenten mellanslagsseparerade; ett argument som SJÄLVT
  # innehåller mellanslag går inte att återskapa och skulle bli två. Ingen
  # av tokenserverns flaggor ser ut så, men gissa inte -- säg ifrån.
  ps -o args= -p "$RUNNING_PID" | tr ' ' '\n' | awk '
    started { print }
    /tokenserver\.py$/ { started = 1 }
  ' | grep -v '^$'
}

if [ -z "$TAIL" ] && [ "$FROM_RUNNING" = 1 ]; then
  RUNNING_PID=""
  TAIL=$(running_tail)
  TAIL="$TAIL
"
  TAIL_SOURCE="tagna från tjänsten som kör"
fi
if [ -z "$TAIL" ]; then
  TAIL=$(inherit_tail)
  [ -z "$TAIL" ] || TAIL="$TAIL
"
  [ -z "$TAIL" ] || TAIL_SOURCE="ärvda från den installerade agenten"
fi

# --publish/--publish-name är bekvämligheten för det enkla fallet. De ersätter
# ett par som redan finns i svansen i stället för att läggas till en gång till.
drop_flag() {
  printf '%s' "$TAIL" | awk -v flag="$1" '
    skip { skip = 0; next }
    $0 == flag { skip = 1; next }
    { print }
  '
}
append_flag() {
  [ -n "$2" ] || return 0
  TAIL=$(drop_flag "$1")
  TAIL="$TAIL
$1
$2
"
  TAIL=$(printf '%s' "$TAIL" | grep -v '^$')
  TAIL="$TAIL
"
}
append_flag --publish "$PUBLISH"
append_flag --publish-name "$PUBLISH_NAME"

ARGS=$(printf '    <string>%s</string>\n' "$(xml "$PYTHON")" "-u" "tokenserver.py")
if [ -n "$TAIL" ]; then
  ARGS="$ARGS
$(printf '%s' "$TAIL" | grep -v '^$' | while IFS= read -r arg; do
    printf '    <string>%s</string>\n' "$(xml "$arg")"
  done)"
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
echo "  python: $PYTHON"
if printf '%s' "$TAIL" | grep -qx -- --publish; then
  echo "  publicerar till reläet: ja"
else
  echo "  publicerar till reläet: NEJ (kör om med --publish <url> om du vill)"
fi
# Flaggorna skrivs ut, utan värden: en installation som tappat --github-repo
# eller ett planmärke syns ingen annanstans än på en sida som slutat visa
# något, och då långt senare.
echo "  flaggor${TAIL_SOURCE:+ ($TAIL_SOURCE)}:$(printf '%s' "$TAIL" |
  grep '^--' | tr '\n' ' ' | sed 's/ $//;s/^/ /')"
echo "  logg: $LOG"
echo
echo "Härefter räcker det här efter en ändring i tools/tokenserver/:"
echo "  launchctl kickstart -k gui/$UID_NUM/$LABEL"
