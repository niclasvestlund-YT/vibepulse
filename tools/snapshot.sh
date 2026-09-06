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

# Alla utcheckningar av repot, inte bara den vi råkar stå i. Kört från en
# länkad worktree under `.worktrees/` — vilket `.gitignore` uttryckligen
# förutser här — pekar `--show-toplevel` på den nästlade katalogen, och både
# defaultmålet och "ligger den inuti repot"-vakten hade då bara jämfört mot
# den. Backupen landade inuti huvudcheckouten, och en `rm -rf` av den tar med
# sig räddningsfilen.
# Bara LEVANDE utcheckningar. En registrering vars katalog raderats står kvar
# som `prunable`, och att gissa liveness ur `[ -d ]` räcker inte: återskapas
# sökvägen som en vanlig katalog passerar den testet, medan `git -C` där dör
# med "not a git repository" — vilket avslutade skriptet med 128 efter att
# bundlen publicerats men före återställningsraderna. Git rapporterar
# `prunable` självt; det är den signalen som gäller, inte en gissning.
# Listan läses NUL-terminerad (`-z`) och lagras i en fil, inte i en variabel:
# en sökväg får innehålla radbrytningar, och då delar en radorienterad parser
# posten mitt itu. Verifierat med en worktree på `/tmp/wt<radbrytning>break` —
# den radorienterade varianten såg `/tmp/wt`, så ett mål inuti den riktiga
# katalogen hade passerat vakten obemärkt. En shellvariabel kan inte bära NUL,
# därför filen.
#
# Parsningen görs i skalet, inte i awk. `RS="\0"` fungerar i mawk och gawk men
# inte i BSD awk — `/usr/bin/awk` på macOS, alltså maintainerns egen maskin —
# där strängar är C-strängar: `"\0"` blir tom sträng, tom RS betyder styckeläge,
# och vakten hade läst fel poster utan att säga ifrån. `read -r -d ""` är samma
# primitiv som resten av filen redan använder, och behöver inget externt verktyg.
# Av samma skäl har varje `mktemp` en egen mall: BSD mktemp kräver en, GNU:s
# gör den valfri, och utan mall dör filen på rad ett på just den maskin där
# AGENTS.md gör den obligatorisk före en historikomskrivning.
part=""; probe=""
roots_file="$(mktemp "${TMPDIR:-/tmp}/tg-snapshot-roots-XXXXXXXX")"
trap 'rm -f "$part" "$roots_file"; rm -rf "$probe"' EXIT
git worktree list --porcelain -z | {
  cur=""; prunable=0
  emit() { if [ -n "$cur" ] && [ "$prunable" = 0 ]; then printf '%s\0' "$cur"; fi; }
  while IFS= read -r -d "" line; do
    case "$line" in
      "worktree "*) emit; cur="${line#worktree }"; prunable=0 ;;
      prunable*)    prunable=1 ;;
    esac
  done
  emit
} > "$roots_file"
# Första posten är huvudutcheckningen. Ett tomt resultat är inte tänkbart —
# git listar alltid minst en — men ett tyst `exit 1` från ett misslyckat `read`
# vore precis den ordlösa vägran som redan bitit en gång i den här filen.
if ! IFS= read -r -d "" repo < "$roots_file"; then
  echo "VÄGRAR: git worktree list gav ingen levande utcheckning att utgå från." >&2
  exit 1
fi
repo="$(cd "$repo" && pwd -P)"
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
# En FLAGGA, inte `exit` i loopen. Loopen läser numera från en fil i stället
# för genom ett rör, alltså körs den i det här skalet — och då avslutade
# `exit 1` hela skriptet direkt och hoppade över meddelandet som skulle
# förklara varför. Vägran blev tyst: exit 1, tomt stderr. Fångat genom att
# faktiskt köra fallet i stället för att läsa diffen.
inside=0
while IFS= read -r -d "" r; do
  [ -n "$r" ] && [ -d "$r" ] || continue
  rp="$(cd "$r" && pwd -P)"
  case "$dest" in "$rp"|"$rp"/*) inside=1; break ;; esac
done < "$roots_file"
if [ "$inside" = 1 ]; then
  if [ "$dest_was_created" = 1 ]; then rmdir "$dest" 2>/dev/null || true; fi
  echo "VÄGRAR: $dest ligger inuti en utcheckning av repot. Sätt TG_SNAPSHOT_DIR utanför." >&2
  exit 1
fi

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
bundle="$dest/$(basename "$repo")-$stamp-${part##*-}.bundle"

# `--quiet` FÖRE filnamnet. Efter det tolkas det som ett rev-list-argument —
# git accepterar det tyst och skriver ändå förloppet till en terminal.
git -C "$repo" bundle create --quiet "$part" --all

# En overifierad backup är ingen backup — men `git bundle verify` räcker inte
# som verifiering. Den läser huvudet och kontrollerar att förutsättningarna
# finns; den rör inte paketets kontrollsummor. En bit vänd mitt i filen ger
# fortfarande "The bundle is okay", medan `git clone` dör på
# "inflate returned -5". Att kalla det verifierat vore precis den falska
# trygghet den här filen finns för att undvika.
#
# Därför indexeras paketet på riktigt: en fetch in i ett tomt bart repo tvingar
# git att packa upp och kontrollsummera varje objekt. Det kostar ~1,3 s på 13
# MB, vilket är gratis jämfört med att upptäcka det efter en omskrivning.
# Refspecarna har skilda mål så de aldrig kan peka på samma ref, och täcker
# även en bundle vars enda head är pseudo-refen HEAD.
#
# `+HEAD:` läggs till bara om bundlen annonserar HEAD. En refspec med `*` är
# tyst när inget matchar, men en EXAKT refspec är det inte: mot en bundle utan
# HEAD — ett repo vars enda refs är remote-refs och taggar — avbryter fetchen
# med "couldn't find remote ref HEAD", och skriptet hade då rapporterat ett
# helt paket som trasigt och raderat backupen. Fel åt fel håll.
probe="$(mktemp -d "${TMPDIR:-/tmp}/tg-snapshot-probe-XXXXXXXX")"
git init -q --bare "$probe"
heads="$(git bundle list-heads "$part")"
specs=('+refs/*:refs/p/*' '+worktrees/*:refs/p-wt/*')
if grep -qx '[0-9a-f]* HEAD' <<<"$heads"; then
  specs+=('+HEAD:refs/p-head/HEAD')
fi
if ! git -C "$probe" fetch "$part" "${specs[@]}" >/dev/null 2>&1; then
  echo "VERIFIERING MISSLYCKADES: paketet gick inte att packa upp." >&2
  echo "  $bundle skrevs aldrig — ingen falsk trygghet." >&2
  exit 1
fi
rm -rf "$probe"

# `ln` publicerar atomiskt OCH vägrar om målet finns — `mv` skriver över, och
# `mv -n` gör tyst ingenting och returnerar 0, vilket vore värst av allt här.
#
# Men hårdlänkar finns inte överallt. exFAT, FAT och SMB-monteringar saknar
# dem — och det är just sådana volymer man hänger på för externa backuper. Ett
# `ln` som alltid failar där hade fått skriptet att påstå att målet redan finns
# och sedan låta trappen radera den färdigverifierade bundlen. Därför skiljs
# de två orsakerna åt: finns målet är det en vägran, annars saknar filsystemet
# hårdlänkar och `mv` är det bästa som går att få. Fönstret mellan kontroll och
# flytt är litet och på en FAT-volym finns ingen atomisk primitiv att välja i
# stället.
if ln "$part" "$bundle" 2>/dev/null; then
  rm -f "$part"
elif [ -e "$bundle" ]; then
  echo "VÄGRAR: $bundle finns redan — skriver inte över en befintlig backup." >&2
  exit 1
else
  mv "$part" "$bundle"
fi

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

# Varningen gäller VARJE levande utcheckning, inte bara huvudworktreen. Sedan
# `repo` blev huvudcheckouten hade en körning från en länkad worktree med
# osparat arbete tigit still — den som stod där hade fått "verifierad" utan
# ett ord om att just deras ändringar ligger utanför.
while IFS= read -r -d "" r; do
  [ -n "$r" ] && [ -d "$r" ] || continue
  # Ett BART repo listas som worktree men har inget arbetsträd: `git status`
  # dör där med 128, och `2>/dev/null` tystar bara meddelandet — statusen går
  # vidare genom `pipefail` och dödade skriptet på tilldelningen nedan. Efter
  # "verifierad", före återställningsraderna, med exit 128 på en backup som
  # faktiskt låg färdig och verifierad på disk. Reproducerat med en bar klon.
  # Ett bart repo har heller inget osparat att varna om.
  if [ "$(git -C "$r" rev-parse --is-bare-repository 2>/dev/null)" = "true" ]; then
    continue
  fi
  # Och går status ändå inte att läsa: säg det. Att tyst rapportera noll
  # ändringar vore en falsk friskförklaring av precis det slag den här filen
  # finns för att undvika — men det får aldrig avsluta skriptet efter att
  # bundlen redan ligger på plats.
  if ! n=$(git -C "$r" status --porcelain 2>/dev/null | wc -l | tr -d ' '); then
    printf '  OBS: kunde inte läsa git status i %s — kontrollera själv vad som ligger osparat där.\n' "$r"
    continue
  fi
  # `if`, inte `[ ] && printf`: när sista utcheckningen är ren returnerar
  # testet 1, hela pipelinen returnerar 1, och under `set -euo pipefail` dog
  # skriptet där — efter att ha skrivit "verifierad" men före
  # återställningsraderna. Det rena enkelworktree-fallet är normalfallet.
  if [ "$n" -gt 0 ]; then
    printf '  OBS: %s ocommittade ändringar i %s ligger UTANFÖR snapshoten.\n' "$n" "$r"
  fi
done < "$roots_file"

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
