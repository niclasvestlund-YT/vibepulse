#!/bin/sh
# Deploya brevlådan (tools/relay/worker.js) till Cloudflare.
#
#   tools/relay/deploy.sh
#
# Varför ett skript och inte bara `wrangler deploy`: den här kodbasen har
# blivit biten två gånger av samma sak — en process som kör kvar gammal kod
# medan filen på disken ser rätt ut (tokenserverns launchd-tjänst, och det
# arkiverade OTA-bygget som frös panelen). Brevlådan är tredje kandidaten:
# ändrar man worker.js händer ingenting alls i verkligheten förrän den är
# deployad, och det syns inte på något annat sätt än att den fria nivån tar
# slut som förut.
#
# Grinden är densamma som OTA-sändarens: kör testerna FÖRST, visa exakt vad
# som går upp, deploya sedan. Skriptet gör inget du inte kunde gjort för
# hand — det ser bara till att stegen kommer i rätt ordning.
#
# Skrivsidan (tools/tokenserver/publisher.py) deployas INTE härifrån: den
# bor i tjänsten på din maskin och kräver en omstart, se sista raden.
set -eu
cd "$(dirname "$0")"

command -v wrangler >/dev/null 2>&1 || {
  echo "hittar inte wrangler — npm i -g wrangler" >&2
  exit 1
}

[ -f wrangler.toml ] || {
  echo "ingen wrangler.toml här — den bor bara på din disk (KV-namespacets" >&2
  echo "id är miljöspecifikt, samma mönster som .ota-device)." >&2
  echo "Skapa om den enligt README.md i den här katalogen." >&2
  exit 1
}

# Testerna först. En brevlåda som slutar slå ihop rätt syns inte på glaset —
# den visar bara äldre siffror, vilket ser ut som att ingenting hänt.
echo "kör brevlådans tester..."
node --test --test-reporter=dot test.mjs

# Vad som faktiskt går upp, alltid utskrivet. Ett okommitterat worker.js är
# helt i sin ordning under utveckling, men det ska stå på skärmen så du vet
# vilken version som blir den som kör.
COMMIT=$(git -C ../.. rev-parse --short HEAD 2>/dev/null || echo "okänd")
if git -C ../.. diff --quiet -- tools/relay/worker.js 2>/dev/null; then
  STATE="som i $COMMIT"
else
  STATE="OKOMMITTERADE ändringar ovanpå $COMMIT"
fi
echo "deployar worker.js ($STATE)"

wrangler deploy

# Röktest: svarar brevlådan fortfarande? URL:en bor redan i secrets.h, samma
# ställe OTA-sändaren hämtar sin token från. Saknas den hoppas steget över —
# det är ett extra öga, inte en grind.
URL=$(sed -n 's/.*TK_VIBEPULSE_RELAY_URL[^"]*"\([^"]*\)".*/\1/p' \
      ../../secrets.h 2>/dev/null | head -1)
if [ -n "$URL" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
         "$URL/api/tokens" || echo 000)
  case "$CODE" in
    200) echo "röktest: brevlådan svarar 200 med siffror" ;;
    404) echo "röktest: brevlådan svarar 404 (\"no data yet\") — vänta på" \
              "nästa publicering, det är inte ett fel efter en deploy" ;;
    429) echo "röktest: 429 — den fria nivån är slut för dygnet." \
              "Deployen gäller, men den märks först efter nollningen." >&2 ;;
    *)   echo "röktest: oväntat svar $CODE från brevlådan" >&2 ;;
  esac
else
  echo "röktest hoppat över (ingen TK_VIBEPULSE_RELAY_URL i secrets.h)"
fi

cat <<'NOTE'

Läs-sidan är nu deployad. Skriv-sidan sitter i tjänsten och kör kvar tills
den startas om:

  launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver

Vill du bevisa att den NYA koden kör: hämta /api/tokens två gånger med
drygt en minut emellan utan att tjänsten publicerat emellan (hjärtslaget är
15 min, så de flesta minuter är tysta). Den nya brevlådan räknar ned
claudeWeekResetMin med en minut; den gamla svarar samma siffra båda
gångerna. Se "Free-tier arithmetic" i README.md.
NOTE
