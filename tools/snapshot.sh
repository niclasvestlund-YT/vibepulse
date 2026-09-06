#!/usr/bin/env bash
# En fil som kan återställa hela repot. Kör den före allt som skriver om
# historik — rebase, filter-repo, force-push — och löpande om du vill.
#
# TÄCKER: varje commit som nås från varje ref. Alla grenar, taggar, notes.
# TÄCKER INTE: ocommittade ändringar, ospårade filer, och allt .gitignore
# döljer. Det betyder att `secrets.h` (WiFi-uppgifter + device key) och
# `.ota-device` INTE ligger här, med flit — en backup som sprider hemligheter
# till en katalog du glömmer bort är en läcka, inte ett skydd. De två filerna
# behöver sin egen plats; se docs/lessons.md.
#
# DEN RADERAR ALDRIG NÅGOT. Första versionen rensade gamla snapshots, med
# motiveringen att en katalog som växer obegränsat slutar bli körd. Den
# rensningen stod sedan för HÄLFTEN av alla buggar granskningen hittade i
# filen — den tog andra repons bundles i en delad katalog, sedan repon som
# råkade heta samma sak, sedan forkar som delar rotcommit. Varje lagning
# öppnade nästa hål. En backup som växer kostar diskutrymme; en som raderar
# fel fil kostar backupen. `rm` den du inte vill ha kvar, själv, när du ser
# vad du gör.
#
# Bakgrunden till den första vakten: 2026-09-06 stoppades en historik-
# omskrivning av att klonen var SHALLOW. `git rev-parse
# --is-shallow-repository` svarar "true" när klonen är avhuggen, vilket läses
# som ett ja om man skummar. En bundle tagen därifrån hade sett komplett ut
# och innehållit en femtedel av historiken.
set -euo pipefail

# Allt den här filen skapar är privat för ägaren. En bundle är HELA repot i en
# fil — varje gren, även opublicerade, och notes — och `git bundle create`
# skriver 0644 under normal umask: mktemps 0600 överlever inte, för git
# återskapar filen. På en delad maskin kunde vem som helst läsa hela
# historiken ur räddningsfilen. Samma resonemang som gäller `secrets.h`:
# säkerhetskopian får inte bli spridningsvägen.
umask 077

repo="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
dest="${TG_SNAPSHOT_DIR:-$(dirname "$repo")/$(basename "$repo")-backups}"

if [ "$(git -C "$repo" rev-parse --is-shallow-repository)" = "true" ]; then
  printf '%s\n' \
    'VÄGRAR: klonen är shallow — en bundle härifrån vore en trunkerad historik' \
    'som ser komplett ut. Kör först:' \
    '  git fetch --unshallow' >&2
  exit 1
fi

# En backup får aldrig bo inuti det den säkerhetskopierar. Jämförelsen sker på
# den UPPLÖSTA sökvägen: `TG_SNAPSHOT_DIR=.snap` och sökvägar med `..` pekar in
# i repot utan att se ut att göra det, och en textjämförelse släpper igenom dem
# — då hamnar räddningsfilen i trädet den ska överleva.
# Städningen vid vägran gäller BARA en katalog den här körningen själv skapade.
# Filen lovar att aldrig radera något; ett `rmdir` som inte skiljer på egen och
# befintlig katalog bryter det löftet för den som redan lagt upp målet och
# råkat peka det fel — rättigheter och annan metadata är hens, inte vår.
dest_was_created=0
[ -d "$dest" ] || dest_was_created=1
mkdir -p "$dest"
dest="$(cd "$dest" && pwd -P)"
case "$dest" in "$repo"|"$repo"/*)
  if [ "$dest_was_created" = 1 ]; then rmdir "$dest" 2>/dev/null || true; fi
  echo "VÄGRAR: $dest ligger inuti repot. Sätt TG_SNAPSHOT_DIR utanför." >&2
  exit 1 ;;
esac

# Namnet får inte kunna kollidera. En sekundstämpel räcker inte — två
# körningar inom samma sekund får identisk sökväg och `git bundle create`
# TRUNKERAR en befintlig fil i stället för att vägra. PID räckte inte heller:
# den är unik bara inom en PID-rymd, så två containrar som delar katalog kan
# få samma. mktemp skapar filen EXKLUSIVT och ger slumpen som suffix; ingen
# `[ -e ]`-kontroll behövs, och den hade ändå varit racy.
#
# Skrivs till ett namn som INTE slutar på .bundle. En avbruten körning lämnar
# då något ingen kan förväxla med en färdig backup.
stamp="$(date +%Y%m%d-%H%M%S)"
part="$(mktemp "$dest/.incomplete-$stamp-XXXXXXXX")"
trap 'rm -f "$part"' EXIT
bundle="$dest/$(basename "$repo")-$stamp-${part##*-}.bundle"

git -C "$repo" bundle create "$part" --all --quiet

# En overifierad backup är ingen backup. Detta läser tillbaka filen och
# kontrollerar att varje objekt den utlovar faktiskt finns i den.
if ! git -C "$repo" bundle verify "$part" >/dev/null 2>&1; then
  echo "VERIFIERING MISSLYCKADES: $bundle skrevs aldrig, ingen falsk trygghet." >&2
  exit 1
fi

# `ln` publicerar atomiskt OCH vägrar om målet finns — `mv` skriver över, och
# `mv -n` gör tyst ingenting och returnerar 0, vilket vore värst av allt här.
if ! ln "$part" "$bundle" 2>/dev/null; then
  echo "VÄGRAR: $bundle finns redan — skriver inte över en befintlig backup." >&2
  exit 1
fi
rm -f "$part"

# Innehållsförteckningen bredvid, så man ser vad en bundle höll utan att packa
# upp den. Den läses ur BUNDLEN, inte ur repot: `git show-ref` listar bara
# vanliga refs, alltså varken `HEAD` från en detached checkout eller
# `worktrees/<namn>/HEAD` från en länkad worktree — precis de heads vars
# commits ingen gren når och som därför är hela poängen med att spara dem. Och
# i ett repo utan vanliga refs returnerar show-ref 1, vilket under `set -e`
# hade dödat skriptet tyst efter att bundlen redan flyttats på plats.
git bundle list-heads "$bundle" > "${bundle%.bundle}.refs"

refs=$(wc -l < "${bundle%.bundle}.refs" | tr -d ' ')
commits=$(git -C "$repo" rev-list --all --count)
size=$(du -h "$bundle" | cut -f1)
printf 'Snapshot: %s\n  %s refs, %s commits, %s — verifierad\n' \
  "$bundle" "$refs" "$commits" "$size"

dirty=$(git -C "$repo" status --porcelain | wc -l | tr -d ' ')
[ "$dirty" -gt 0 ] && printf '  OBS: %s ocommittade ändringar ligger UTANFÖR snapshoten.\n' "$dirty"

# Sökvägen citeras: en katalog med mellanslag hade annars gjort raden obrukbar
# i precis det läge man klistrar in den utan att tänka.
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# INGET fullständigt återställningsrecept skrivs ut här, och det är ett beslut
# taget efter åtta granskningsrundor där nästan varje ny kant satt i just de
# raderna: refspecar som tappade taggar och notes, ett grennamn som kunde
# innehålla `$(...)`, en detached HEAD utan namn, en länkad worktree, och till
# sist ett räddningsnamn som kolliderade med sig självt så fort man återställt
# en gång. En återställning görs sällan, av en människa, en gång — den tål att
# slås upp. Ett recept som är fel i det läget gör skada.
#
# Det som står kvar är sant utan förbehåll: filen, vad den innehåller, och det
# enda kommandot som inte kan bli fel.
printf '\nInnehåll:  git bundle list-heads %s\n' "$(q "$bundle")"
printf '           (eller läs %s)\n' "$(basename "${bundle%.bundle}.refs")"
printf 'Återställ: git clone %s <katalog>\n' "$(q "$bundle")"
printf '           Klonen tar grenar och taggar. Innehåller listan ovan\n'
printf '           HEAD eller worktrees/... är de commits ingen gren når;\n'
printf '           hämta dem med en egen refspec, se docs/lessons.md.\n'
