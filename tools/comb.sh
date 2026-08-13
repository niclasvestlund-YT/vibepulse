#!/bin/sh
# Schemalagd loggkamning (docs/observability.md): körs veckovis av
# launchd via tools/se.torget.comb.plist, eller för hand:
#
#     sh tools/comb.sh
#
# Två lager, med avsiktlig degradering:
#   1. Röktestet — deterministiskt, gratis, alltid. FAIL ger en
#      macOS-notis direkt.
#   2. Agentkammen — `claude -p` följer kamrutinens omdömessteg och för
#      in äkta fynd i docs/observability-backlog.md (ocommittat: du
#      granskar och committar själv). Saknas claude-CLI:t körs bara
#      röktestet, och rapporten säger det.
#
# Rapporter: ~/Library/Logs/torget-comb/comb-<datum>.log (12 senaste
# behålls). Notiser skickas bara vid FAIL, agentfel eller fynd — en ren
# vecka är tyst.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# launchd:s PATH är minimal — claude bor typiskt i någon av de här.
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH

STAMP="$(date +%Y-%m-%d-%H%M)"
OUT_DIR="$HOME/Library/Logs/torget-comb"
REPORT="$OUT_DIR/comb-$STAMP.log"
mkdir -p "$OUT_DIR"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"VibePulse-kammen\"" >/dev/null 2>&1 || true
}

cd "$REPO" || exit 1
echo "== VibePulse-kamning $STAMP (rev $(git rev-parse --short HEAD 2>/dev/null || echo '?')) ==" > "$REPORT"

echo "-- röktest --" >> "$REPORT"
SMOKE_OUT="$(python3 tools/tokenserver/smoke.py 2>&1)"
SMOKE_EXIT=$?
echo "$SMOKE_OUT" >> "$REPORT"
if [ "$SMOKE_EXIT" -eq 2 ]; then
  notify "Röktestet hittade FEL — läs comb-$STAMP.log"
fi

if command -v claude >/dev/null 2>&1; then
  echo "-- agentkam --" >> "$REPORT"
  claude -p "Comb the logs. Follow the comb routine in docs/observability.md top to bottom. Steps 1-4 already ran as the smoke test; its output is below. Do the remaining judgment steps as far as possible without hardware (skip the physical screen and serial console, and say you skipped them). File genuinely NEW findings as entries in docs/observability-backlog.md and update its 'Last combed:' line to today's date. Edit files but do NOT commit or push — the maintainer reviews. End your reply with exactly one line: 'VERDICT: rent' if nothing needs attention, otherwise 'VERDICT: fynd — <one sentence>'.

Smoke output (exit $SMOKE_EXIT):
$SMOKE_OUT" \
    ${CLAUDE_FLAGS:---permission-mode acceptEdits} >> "$REPORT" 2>&1
  CLAUDE_EXIT=$?
  if [ "$CLAUDE_EXIT" -ne 0 ]; then
    notify "Agentkammen misslyckades (exit $CLAUDE_EXIT) — läs comb-$STAMP.log"
  else
    VERDICT="$(grep '^VERDICT:' "$REPORT" | tail -1)"
    case "$VERDICT" in
      "VERDICT: rent"*) : ;;  # ren vecka: tyst
      VERDICT:*) notify "Kammen fann något — läs comb-$STAMP.log" ;;
      *) notify "Kammen gav ingen VERDICT — läs comb-$STAMP.log" ;;
    esac
  fi
else
  echo "claude-CLI saknas i PATH — bara röktestet kördes" >> "$REPORT"
  [ "$SMOKE_EXIT" -eq 0 ] || notify "Röktest med anmärkningar och inget claude-CLI — läs comb-$STAMP.log"
fi

# Behåll de 12 senaste rapporterna.
ls -t "$OUT_DIR"/comb-*.log 2>/dev/null | tail -n +13 | while read -r f; do
  rm -f "$f"
done

exit 0
