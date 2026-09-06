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

repo="$(git rev-parse --show-toplevel)"
dest="${TG_SNAPSHOT_DIR:-$(dirname "$repo")/$(basename "$repo")-backups}"
keep="${TG_SNAPSHOT_KEEP:-10}"

if [ "$(git -C "$repo" rev-parse --is-shallow-repository)" = "true" ]; then
  printf '%s\n' \
    'VÄGRAR: klonen är shallow — en bundle härifrån vore en trunkerad historik' \
    'som ser komplett ut. Kör först:' \
    '  git fetch --unshallow' >&2
  exit 1
fi

# En backup får aldrig bo inuti det den säkerhetskopierar.
case "$dest" in "$repo"|"$repo"/*)
  echo "VÄGRAR: $dest ligger inuti repot. Sätt TG_SNAPSHOT_DIR utanför." >&2
  exit 1 ;;
esac

mkdir -p "$dest"
stamp="$(date +%Y%m%d-%H%M%S)"
bundle="$dest/$(basename "$repo")-$stamp.bundle"

git -C "$repo" bundle create "$bundle" --all --quiet

# En overifierad backup är ingen backup. Detta läser tillbaka filen och
# kontrollerar att varje objekt den utlovar faktiskt finns i den.
if ! git -C "$repo" bundle verify "$bundle" >/dev/null 2>&1; then
  echo "VERIFIERING MISSLYCKADES: $bundle — tas bort, ingen falsk trygghet." >&2
  rm -f "$bundle"
  exit 1
fi

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
ls -1t "$dest"/*.bundle 2>/dev/null | tail -n "+$((keep+1))" | while read -r old; do
  rm -f "$old" "${old%.bundle}.refs"
  echo "  rensade $(basename "$old")"
done

printf '\nÅterställ:  git clone %s <katalog>\n' "$bundle"
printf 'Eller in i ett befintligt repo:\n'
printf "  git fetch %s '+refs/heads/*:refs/heads/*'\n" "$bundle"
