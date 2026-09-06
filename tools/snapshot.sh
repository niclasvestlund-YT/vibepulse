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
mkdir -p "$dest"
dest="$(cd "$dest" && pwd -P)"
case "$dest" in "$repo"|"$repo"/*)
  rmdir "$dest" 2>/dev/null || true
  echo "VÄGRAR: $dest ligger inuti repot. Sätt TG_SNAPSHOT_DIR utanför." >&2
  exit 1 ;;
esac

# Sekundstämpeln ensam räcker inte som namn: två körningar inom samma sekund
# — parallella worktrees, ett skript som loopar — får identisk sökväg, och
# `git bundle create` TRUNKERAR en befintlig fil i stället för att vägra.
stamp="$(date +%Y%m%d-%H%M%S)-$$"
bundle="$dest/$(basename "$repo")-$stamp.bundle"
[ -e "$bundle" ] && { echo "VÄGRAR: $bundle finns redan." >&2; exit 1; }

# Skriv först till ett namn som INTE slutar på .bundle, verifiera, och flytta
# på plats sist. En avbruten körning lämnar då en .part som ingen kan förväxla
# med en färdig backup — i stället för en halvskriven fil med rätt namn.
part="$dest/.incomplete-$stamp.part"
trap 'rm -f "$part"' EXIT

git -C "$repo" bundle create "$part" --all --quiet

# En overifierad backup är ingen backup. Detta läser tillbaka filen och
# kontrollerar att varje objekt den utlovar faktiskt finns i den.
if ! git -C "$repo" bundle verify "$part" >/dev/null 2>&1; then
  echo "VERIFIERING MISSLYCKADES: $bundle skrevs aldrig, ingen falsk trygghet." >&2
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

# Sökvägen citeras: en katalog med mellanslag hade annars gjort de utskrivna
# kommandona obrukbara i precis det läge man klistrar in dem utan att tänka.
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
qb="$(q "$bundle")"


# Refspecen är `refs/*`, inte `refs/heads/*`: bundlen sparar VARJE ref, så en
# återställning som bara tar grenar lämnar taggar, notes och egna refs tyst
# borta — de som är svårast att märka att man saknar. `git clone` gör samma
# sak: den tar grenar och taggar men inte notes eller egna namnrum, så den
# fullständiga vägen står först. Den vägen går via ett BART repo: ett vanligt
# `git init` sätter HEAD på refs/heads/master, och en fetch dit nekas med
# "refusing to fetch into branch ... checked out" även när refen inte finns
# än. Den varianten skrevs, testades och underkändes här.
#
# Inget grennamn interpoleras in i de utskrivna kommandona. Två skäl, båda
# funna i granskning: ett grennamn får innehålla `$(...)`, och git accepterar
# det — den som klistrar in raden kör då kommandot i stället för att återställa
# grenen. Och namnet fanns inte alltid: från en detached HEAD gav uppslaget
# ingenting, fallbacken pekade på en gren som kunde saknas, och bundlens lösa
# commit — som ligger under pseudo-refen `HEAD`, inte under `refs/*` — hade
# inte fått någon ref alls. Ett FAST räddningsnamn löser båda: `HEAD` hämtas
# uttryckligen, och HEAD pekas på det, oavsett vad grenen heter.
printf '\nÅterställ ALLT till ett nytt repo:\n'
printf '  git init --bare <katalog>.git\n'
printf "  git -C <katalog>.git fetch %s '+refs/*:refs/*' '+HEAD:refs/heads/rescue-head'\n" "$qb"
printf '  git -C <katalog>.git symbolic-ref HEAD refs/heads/rescue-head\n'
printf '  git clone <katalog>.git <katalog>       # arbetskopia\n'
printf '\nEller in i ett befintligt repo, utan att röra utcheckade grenar:\n'
printf "  git fetch %s '+refs/*:refs/rescue/*' '+HEAD:refs/rescue/HEAD'\n" "$qb"
printf '  git for-each-ref refs/rescue                # se vad som fanns\n'
printf '  git reset --hard refs/rescue/heads/<gren>   # när du valt\n'
printf '\n  (git clone %s går också, men tar bara grenar och taggar.)\n' "$qb"
