#!/usr/bin/env bash
# En fil som kan återställa hela repot. Kör den före allt som skriver om
# historik — rebase, filter-repo, force-push — och löpande om du vill.
#
# TÄCKER: varje commit som nås från varje ref. Alla grenar, alla taggar.
# TÄCKER INTE: ocommittade ändringar, ospårade filer, och allt .gitignore
# döljer. Det betyder att `secrets.h` (WiFi-uppgifter + device key) och
# `.ota-device` INTE ligger här, med flit — en backup som sprider hemligheter
# till en katalog du glömmer bort är en läcka, inte ett skydd. De två filerna
# behöver sin egen plats; se docs/lessons.md.
#
# Bakgrunden: 2026-09-06 stoppades en historikomskrivning av att klonen var
# SHALLOW. `git rev-parse --is-shallow-repository` svarar "true" när klonen är
# avhuggen, vilket läses som ett ja om man skummar. En bundle tagen därifrån
# hade sett komplett ut och innehållit en femtedel av historiken. Därför är
# det första den här filen gör att vägra i det läget.
set -euo pipefail

repo="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
dest="${TG_SNAPSHOT_DIR:-$(dirname "$repo")/$(basename "$repo")-backups}"
keep="${TG_SNAPSHOT_KEEP:-10}"

if [ "$(git -C "$repo" rev-parse --is-shallow-repository)" = "true" ]; then
  printf '%s\n' \
    'VÄGRAR: klonen är shallow — en bundle härifrån vore en trunkerad historik' \
    'som ser komplett ut. Kör först:' \
    '  git fetch --unshallow' >&2
  exit 1
fi

# Noll behållna snapshots är ingen snapshot: `tail -n "+1"` hade matat in den
# nyss skapade bundlen i rensningen längst ned, avslutat med 0 och ändå skrivit
# ut en återställningsrad för en fil som inte finns kvar. Fångas före allt
# arbete, inte efter.
case "$keep" in ''|*[!0-9]*)
  echo "VÄGRAR: TG_SNAPSHOT_KEEP måste vara ett heltal ≥ 1 (fick: $keep)." >&2
  exit 1 ;;
esac
if [ "$keep" -lt 1 ]; then
  echo "VÄGRAR: TG_SNAPSHOT_KEEP=$keep skulle radera snapshoten den just tog." >&2
  exit 1
fi

# En backup får aldrig bo inuti det den säkerhetskopierar. Jämförelsen måste
# ske på den UPPLÖSTA sökvägen: `TG_SNAPSHOT_DIR=.snap` och sökvägar med `..`
# pekar in i repot utan att se ut att göra det, och en textjämförelse släpper
# igenom dem — då hamnar räddningsfilen i trädet den ska överleva.
mkdir -p "$dest"
dest="$(cd "$dest" && pwd -P)"
case "$dest" in "$repo"|"$repo"/*)
  rmdir "$dest" 2>/dev/null || true
  echo "VÄGRAR: $dest ligger inuti repot. Sätt TG_SNAPSHOT_DIR utanför." >&2
  exit 1 ;;
esac

# Varje repo får ett eget namnrum under målkatalogen, döpt efter sin ÄLDSTA
# commit — repots identitet, oberoende av var det är utcheckat. Basnamnet
# räcker inte: två olika repon som råkar heta samma sak i en delad katalog
# skrev till samma filnamn samma sekund och det ena skrev över det andras
# enda backup. Två utcheckningar av SAMMA repo delar namnrum, vilket är rätt
# — det är samma historia.
repo_id="$(git -C "$repo" rev-list --max-parents=0 --all 2>/dev/null \
  | sort | head -1 | cut -c1-12)"
dest="$dest/$(basename "$repo")-${repo_id:-norootcommit}"
mkdir -p "$dest"
# Sekundstämpeln ensam räcker inte som namn: två körningar inom samma sekund
# — parallella worktrees, ett skript som loopar — får identisk sökväg, och
# `git bundle create` TRUNKERAR en befintlig fil i stället för att vägra. Den
# senare körningen kan alltså skriva över den enda kända goda bundlen. PID:en
# gör namnet unikt.
stamp="$(date +%Y%m%d-%H%M%S)-$$"
bundle="$dest/$(basename "$repo")-$stamp.bundle"

# Skriv först till ett namn som INTE slutar på .bundle, verifiera, och flytta
# på plats sist. En avbruten körning lämnar då en .part som varken rensningen
# eller en människa kan förväxla med en färdig backup — i stället för en
# halvskriven fil med rätt namn.
part="$dest/.incomplete-$stamp.part"
trap 'rm -f "$part"' EXIT

git -C "$repo" bundle create "$part" --all --quiet

# En overifierad backup är ingen backup. Detta läser tillbaka filen och
# kontrollerar att varje objekt den utlovar faktiskt finns i den.
if ! git -C "$repo" bundle verify "$part" >/dev/null 2>&1; then
  echo "VERIFIERING MISSLYCKADES: $bundle — tas bort, ingen falsk trygghet." >&2
  exit 1
fi
mv "$part" "$bundle"

# Reflistan bredvid, så man ser vad en bundle höll utan att packa upp den.
git -C "$repo" show-ref > "${bundle%.bundle}.refs"

refs=$(wc -l < "${bundle%.bundle}.refs" | tr -d ' ')
commits=$(git -C "$repo" rev-list --all --count)
size=$(du -h "$bundle" | cut -f1)
printf 'Snapshot: %s\n  %s refs, %s commits, %s — verifierad\n' \
  "$bundle" "$refs" "$commits" "$size"

dirty=$(git -C "$repo" status --porcelain | wc -l | tr -d ' ')
[ "$dirty" -gt 0 ] && printf '  OBS: %s ocommittade ändringar ligger UTANFÖR snapshoten.\n' "$dirty"

# Behåll de nyaste; en backupkatalog som växer obegränsat slutar bli körd.
# Rensningen är begränsad till DET HÄR repots egna bundles: pekar
# TG_SNAPSHOT_DIR på en delad katalog skulle ett bredare glob radera andra
# repons enda säkerhetskopior, tyst och som en bieffekt av att vi tog vår.
ls -1t "$dest"/*.bundle 2>/dev/null \
  | tail -n "+$((keep+1))" | while read -r old; do
  rm -f "$old" "${old%.bundle}.refs"
  echo "  rensade $(basename "$old")"
done

# Återställningen som faktiskt fungerar. `git fetch <bundle>
# '+refs/heads/*:refs/heads/*'` in i en vanlig klon avbryter med "refusing to
# fetch into branch ... checked out" så fort den utcheckade grenen finns i
# bundlen — vilket den alltid gör i det läge man behöver den här filen. Därför
# hämtas allt till ett eget namnrum, där det går att titta på före man skriver
# över något. Refspecen är `refs/*`, inte `refs/heads/*`: bundlen sparar VARJE
# ref, så en återställning som bara tar grenar lämnar taggar, notes och egna
# refs tyst borta — precis de som är svårast att märka att man saknar.
printf '\nÅterställ till en ny katalog:\n  git clone %s <katalog>\n' "$bundle"
printf '\nEller in i ett befintligt repo, utan att röra utcheckade grenar:\n'
printf "  git fetch %s '+refs/*:refs/rescue/*'\n" "$bundle"
printf '  git log --oneline refs/rescue/heads/main   # se vad som fanns\n'
printf '  git reset --hard refs/rescue/heads/<gren>  # när du valt\n'
printf '  (taggar hamnar under refs/rescue/tags/, notes under .../notes/)\n'
