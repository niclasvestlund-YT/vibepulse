# tokenserver — VibePulse computer service

> **English quickstart:** `python3 tokenserver.py`. Python 3.11+ is required;
> the core service uses only the standard library,
> nothing to install. It reads your local Claude Code/Codex logs and serves
> `/api/tokens` + `/api/agent-status` + `/api/max-tracker` on port 8737 for
> the screen. Add `--github-repo owner/repository` for the optional public
> GitHub Stars/Forks feed at `/api/github`; it needs no token. Add
> `--claude-plan {pro,max5x,max20x}` and/or `--codex-plan
> {plus,pro}` to show a plan badge on the Max Tracker pages; both flags are
> optional and purely cosmetic (a display label, never used in any
> percentage math). For autostart on login, follow
> [Autostart via launchd](#autostart-via-launchd) and verify the live revision;
> do not copy an old plist between checkouts. Default privacy contract: only percentages
> and counts are served. Optional local interactions can transiently serve
> bounded detail, but prompts, commands and file contents are not stored.
> Full details below in Swedish. Your agent translates.

## Optional panel interactions (English quickstart)

The computer must be awake and the tokenserver must be running for fresh data
or answers. Direct LAN works only when the panel can reach the computer. The
numbers relay is separate and still publishes numbers only. The optional
encrypted interaction relay lets both sides use unrelated Wi-Fi with outbound
HTTPS; it is off by default and is not enabled by the Codex plugin.

From the repository root, use the guided setup:

```sh
python3 tools/vibepulse_setup.py install
python3 tools/vibepulse_setup.py status
python3 tools/vibepulse_setup.py doctor
python3 tools/vibepulse_setup.py disable codex
python3 tools/vibepulse_setup.py uninstall codex
```

Install offers four independent choices: Claude, Codex, both, or neither.
Installing the plugin does not turn Codex on. It also asks separately whether
bounded question/command detail may reach the local panel; no is the default.
After installation, open Codex, run `/hooks`, review the VibePulse
`SessionStart` and `PermissionRequest` hooks, and explicitly trust them. Then
**Start a new Codex task** so the hooks and MCP tool are freshly loaded. Doctor
reports trust/setup problems but does not bypass review.

From plugin `0.1.7`, the trusted `SessionStart` hook also makes a sub-second,
read-only request to fixed loopback endpoints. It reports one sanitized fault
class—provider data, device path, local service, or plugin/server source
drift—instead of letting every failure collapse into the same `STALE` advice.
It never restarts the service or treats a failed check as approval. Detailed
repair still starts with `doctor` and `tools/tokenserver/smoke.py`.

The same doctor path reads a content-free Claude credential guard from
`GET /`: `ready`, `expiring`, `expired`, `unavailable`, or `unknown`, plus
whole minutes remaining when known. It warns 30 minutes before expiry and
never returns or logs an access token, refresh token, account id, or Keychain
body. Read it together with `claudeProbe` and `/api/tokens`: when the probe is
`usage_http_200 + ok` and the relevant stale flag is false, a current Claude
client is feeding live quota even if the saved fallback says `expired`. A new
Claude Code CLI turn is the supported way to refresh that fallback; the
service detects it within 15 seconds without retrying a dead token upstream or
needing a restart.

The Codex safe-command tier only offers **ALLOW ONCE** for recognized
read-only, test, and build commands. Unknown, mutating, secret-bearing, or
truncated commands use the computer fallback. Questions do too unless Codex
provides two or three choices and marks exactly one recommendation. Timeout or
silence always falls back; it never approves.

`python3 tools/vibepulse_setup.py disable codex` keeps the package installed
but disables only Codex. `python3 tools/vibepulse_setup.py uninstall codex`
removes only the VibePulse Codex adapter and preserves Claude, relay, GitHub,
device-key, and unrelated Codex settings. The shared key and repository are not
deleted.

The old tokenserver `--interactions` flag is a legacy alias for Claude only.
Prefer the saved provider choices above. **legacy Claude v1 is insecure**: it
does not bind a verdict to provider and the exact view, so it is off by default,
Claude-only, and never suitable for Codex.

Both autostart launchers start `tokenserver.py` without provider/detail flags.
The service reads its saved config at startup; changing a choice does not
require editing a launchd plist or Task Scheduler command. Restart the service
after changing saved choices if it is already running.

For decisions across isolated Wi-Fi, read
[`docs/interaction-relay.md`](../../docs/interaction-relay.md), then use the
separate lifecycle:

```sh
python3 -m pip install -r requirements-interaction-relay.txt
cd tools/interaction-relay && npm ci && npx wrangler login && cd ../..
python3 tools/vibepulse_setup.py relay install --url HTTPS_ORIGIN --yes-e2e-cloud
python3 tools/vibepulse_setup.py relay status
python3 tools/vibepulse_setup.py relay doctor
python3 tools/vibepulse_setup.py relay disable
python3 tools/vibepulse_setup.py relay uninstall --keep-worker
python3 tools/vibepulse_setup.py relay uninstall --delete-worker
```

Installing the Codex plugin does not enable the encrypted interaction relay.
The tokenserver's `--publish` option remains numbers-only. Its separate
`--interaction-relay HTTPS_ORIGIN` option carries bounded E2E ciphertext;
question, command, project, and verdict content is encrypted locally before
it reaches the user-owned mailbox. Prefer `tools/vibepulse_setup.py`, which
stores these choices without putting secrets or feature flags in a service
command line.

Serverar Claude- och Codex-användningen som platt JSON enligt glance-
mönstret (kontrakt v2). Skärmen hämtar `/api/tokens` över LAN var 30:e
sekund. Ren Python 3-stdlib — inget att installera. Tre källor:

1. **Volymen** — `~/.claude/projects/**/*.jsonl` skannas inkrementellt:
   dagens/månadens tokens, brinntakt, sessioner.
2. **Claudes tak** (Clawdmeter-mönstret) — tjänsten läser Claude Desktops
   aktiva, injicerade OAuth-token eller Claude Codes nyckelringsfallback
   (på Windows i stället `%USERPROFILE%\.claude\.credentials.json`, samma
   post — se [Windows](#windows) nedan) och gör en minimal API-förfrågan
   (`max_tokens: 0` — prefill utan output, i praktiken gratis) var 240:e
   sekund; rate-limit-headrarna i svaret bär usage-panelens tre fönster:
   5-timmars, veckan och veckan för tyngsta modellen (Fable/Opus).
   Tokenen lämnar aldrig datorn — skärmen får bara procenttal. Om de
   tokenkopior tokenservern kan läsa har gått ut men den officiella Claude
   Desktop-klienten fortfarande arbetar, används dess innehållsfria
   `plan-usage-history.json` passivt för den generella veckan. Filen måste
   vara högst 20 minuter gammal och en tidigare autentiserad cachepost måste
   fortfarande bära poolens giltiga reset. Modellveckan gissas aldrig och
   förblir därför stale tills OAuth-proben återhämtar sig.
3. **Codex tak** — tjänsten frågar Codex lokala, skrivskyddade app-server via
   `account/rateLimits/read`, alltså samma aktuella snapshot som Codex-panelen
   visar. Om app-servern saknas används en passiv fallback: begränsad läsning
   av de 20 nyaste `~/.codex/sessions/**/rollout-*.jsonl` (högst den sista MiB
   per fil). Bara Codex faktiska `event_msg`/`token_count`-händelse med ett
   direkt `payload.rate_limits` accepteras i fallbacken.

Generella veckotak hålls strikt åtskilda från namngivna modellkvoter. För
Codex måste `limit_name` saknas, vara null eller vara en tom sträng och
`window_minutes` vara exakt 10080 minuter. Ett 30-dagarsfönster (43200)
publiceras inte under den nuvarande WEEKLY-kontraktet. Spark och andra
namngivna kvoter kan därför aldrig ersätta WEEK. För Claude är exakt `7d` eller
`week` den generella veckan; Fable, Opus, Sonnet och explicit `model` är
modellveckan. Ett okänt namn som `7d_haiku` blir endast ett sanerat namn i
rotendpointens diagnostik, aldrig ett kvotvärde.

## Prova

```
python3 tokenserver.py
curl http://localhost:8737/api/tokens
```

## Valfri GitHub-sida och stjärnhändelser

Ett publikt repo kan övervakas utan token:

```
python3 tokenserver.py --github-repo owner/repository
curl http://localhost:8737/api/github
```

Miljövariabeln `VIBEPULSE_GITHUB_REPO=owner/repository` är likvärdig och
passar launchd. Första lyckade pollen sätter bara utgångsvärdet; gamla
stjärnor spelas inte upp som nya. Därefter pollas repo-metadata varannan
minut. Vid en ökning hämtas senaste publika stargazer för namnet i popupen;
om den läsningen misslyckas publiceras ändå det auktoritativa nya antalet
med anonym aktör.

GitHub-monitorn har egen tråd, timeout och minst tio minuters fel-backoff.
Dess senaste goda värden markeras `stale`; fel går aldrig via token-, agent-
eller Max Tracker-flödet. Utan `--github-repo` svarar endpointen explicit
`{"v": 1, "enabled": false}`. Skärmens sida och popup slås sedan på
oberoende i `secrets.h`; se `secrets.h.example`.

## Agentstatus

`/api/agent-status` är ett separat v2-kontrakt för Claude Codes och Codex
pågående aktivitet. En bakgrundstråd följer de senast aktiva JSONL-filerna
inkrementellt var 0,5 sekund: Claude under `~/.claude/projects` och Codex
under `~/.codex/sessions`. HTTP-tråden läser bara en låst minnesbild; den
skannar eller öppnar aldrig sessionsfiler på begäran.

```json
{
  "v": 2,
  "seq": 12,
  "agents": {
    "claude": {
      "active_count": 2,
      "jobs": [{
        "task_id": "opaque-session-hash",
        "event_id": "4e50fb6a90293d167abb52a531c581fd",
        "state": "working",
        "project": "Torget",
        "activity": "testing",
        "model": "FABLE 5",
        "effort": "XHIGH",
        "updated_ms": 240
      }]
    },
    "codex": {
      "active_count": 0,
      "jobs": [{
        "task_id": "turn-id",
        "event_id": "c625c56abf9d8d7f13408a21179198c0",
        "state": "done",
        "project": null,
        "activity": null,
        "model": "GPT-5.6 SOL",
        "effort": "XHIGH",
        "updated_ms": 1900
      }]
    }
  }
}
```

- Varje provider innehåller högst fyra prioriterade publika `jobs`, men
  `active_count` räknar samtliga kända `working`, `waiting` och `error` även
  när listan är full. Servern håller högst 16 metadatajobb per provider.
- Jobb rangordnas `waiting`, `error`, `working`, `done`; nyare jobb går före
  inom samma tillstånd. `seq` ökar när lagrad publik status ändras.
- `task_id` är sessionsloggens opaka uppgiftsidentitet och `event_id` ett
  stabilt hash-id för leverantör, uppgift, tillstånd och källhändelse.
- `state` är `idle`, `working`, `waiting`, `done`, `error` eller `unknown`.
  `activity` är en grov kategori som exempelvis `thinking`, `reading`,
  `editing`, `searching`, `running`, `testing`, `building`,
  `waiting_input` eller `waiting_approval`.
- `project` är endast en kontrollteckenrensad basename på högst 16 UTF-8-byte;
  `task_id` är ett opakt, kollisionssäkert id på högst 64 UTF-8-byte.
  `updated_ms` är tiden sedan händelsens säkra tidsstämpel (filens mtime är
  reserv när tidsstämpel saknas), inte tiden då servern råkade starta.
- Ett `working`-jobb som inte uppdaterats på 120 sekunder faller ur den
  publika listan som okänt. Det skrivs aldrig om till ett påhittat `done`,
  och enbart läsning av status ökar inte `seq`.

Integritetsgränsen är avsiktligt hård: klassificeraren kan lokalt titta på
verktygsnamn och ett kommando för att skilja test, bygge och vanlig körning,
men varken promptar, kommandon, meddelandetext, filinnehåll eller råa
logghändelser sparas eller exponeras. Ofullständiga sista rader hålls lokalt
till nästa append, men aldrig över 1 MiB. Giltiga JSONL-rader på högst 1 MiB
klassificeras; större eller felaktigt UTF-8-kodade rader kastas till nästa
radbrytning så att en senare giltig händelse fortfarande kan läsas. Läsningen
sker i 64 KiB-block och tar högst 1 MiB eller 256 poster per fil och poll; stora
historiska filer dräneras därför över flera pollar utan en obunden minnestopp.

De tolv aktiva kandidatfilerna per leverantör kontrolleras var 0,5 sekund. Den
rekursiva upptäckten av nya sessioner görs däremot högst var femte sekund, och
återanvänder samma stat-resultat för urval och identitetsavstämning. Högst 48
filidentiteter behålls; råa partialbuffertar och sökvägsalias för kalla filer
släpps. Kort rotation eller tillfällig frånvaro kan ändå återanvända
inode-bundet offset och digest utan historikreplay.

Oförändrade snabba pollar öppnar inte filen. Append kontrollerar bara ett
begränsat prefixprov, medan en ny full SHA-256-verifiering startar högst en gång
per fem sekunder och läser högst 1 MiB per poll. Stora prefix verifieras alltså
stegvis.
Shrink, inodebyte och misstänkta signaturer hanteras omedelbart; en senare
fullträff på en omskrivning återställer följaren och spelar ersättningen exakt
en gång. Det ger eventual omskrivningsdetektering utan kvadratisk livstids-I/O,
och inget färdigt radinnehåll sparas.

Varje befintlig fil som ses för första gången börjar som backfill, även om hela
filen når EOF under första läsningen. Detsamma gäller efter en återställning,
ett inodebyte på samma sökväg eller när en tidigare följd kall fil återupptäcks.
Historiska mellanlägen publiceras inte under tiden. Endast den säkert senaste
klassificerade metadatahändelsen per leverantör och sökväg hålls kompakt i minnet
och appliceras högst en gång när backloggen är tömd; om slutstatus redan är
publik ändras varken `seq` eller dess observationstid. Därefter behandlas nya
kompletta append-poster åter normalt. Återupptäcktsmarkörerna är begränsade till
96 sökvägar och innehåller inga råa loggposter.

Lokalt röktest från repots rot, på en alternativ port:

```
python3 tools/tokenserver/tokenserver.py --port 8738
curl http://127.0.0.1:8738/api/agent-status
```

## Max Tracker

`/api/max-tracker` serverar kontrakt v1 för de två heatmap-sidorna: dagens
kvot-topp per leverantör de senaste 20 ISO-veckorna, plus STREAK/MAX WEEKS/
AVG PEAK/MAX DAYS-aggregat. Flaggorna `--claude-plan {pro,max5x,max20x}` och
`--codex-plan {plus,pro}` är frivilliga och renderar bara en muted badge
("PRO", "MAX 5X", "MAX 20X", "PLUS") i sidans hörn — ogiltiga värden avvisas
direkt av argparse (`SystemExit`), och etiketten påverkar aldrig någon
procenträkning.

Data kommer från två oberoende kanaler i `tools/tokenserver/max_tracker.py`
(`MaxTrackerStore`):

- **Backfill**: en bakgrundstråd kör `backfill_step()` var 0,5:e sekund
  (samma kadens som agentstatusens poll), obundet i tiden — den fortsätter
  ticka för evigt istället för att stänga av sig efter att ha hunnit ikapp,
  eftersom ett tomt steg bara läser filsystemets stat-info och därför är
  billigt nog att köra hur ofta som helst; det gör att en helt ny rollout-
  eller sessionsfil upptäcks automatiskt utan omstart. Codex-kvoten
  rekonstrueras ur `~/.codex/sessions/**/rollout-*.jsonl`; Claude-kvoten är
  inte rekonstruerbar i efterhand, så historiska Claude-dagar får bara
  aktivitet och volymnivå, aldrig procent.
- **Löpande observation**: samma ställen som redan publicerar en färsk,
  icke-cachad, icke-`stale`-procent till `/api/tokens` (Claude-proben var
  240:e sekund, Codex läsning via app-servern eller rollout-fallbacken, och
  den befintliga dagsvolym-uppräkningen) matar också dagens topp här —
  aldrig ett `*Stale: true`-värde ur kvotcachen. Sessionsfönstret (5 h,
  300 min) och det generella veckofönstret (10 080 min) hålls isär enligt
  samma >600-minutersregel som redan används för Claude/Codex-kvoterna;
  Codex bär sitt fönster i klartext i egen data, Claude klassas på samma
  sätt som `/api/tokens` redan gör det.

Toppnivåfältet `stale` speglar exakt samma regel som `claudeWeekStale`/
`codexWeekStale` ovan (ingen ny klocka) — sant när servern inte lyckats
uppdatera den generella veckokvoten på sistone.

Persistens: `~/Library/Application Support/VibePulse/max-tracker.json`,
atomisk skrivning i läge 0600, 400 dagars gles retention. Sparningen körs
asynkront på en egen bakgrundstråd som töms tills ingen ändring väntar —
samma mönster som kvotcachens skrivare, fast med en enda sammanslagen
sparning istället för en post i taget.

```
curl http://127.0.0.1:8738/api/max-tracker
```

När Claude Desktop körs används dess färska processtoken utan dialog. Vid
fristående Claude Code kan första körningen fråga macOS om nyckelringsåtkomst
("security vill använda ... Claude Code-credentials") — välj "Tillåt alltid"
så tjänsten kan probea utan att fråga om. Startloggen skriver dessutom ut de exakta
`anthropic-ratelimit-*`-headrarna vid första proben — facit om mappningen
någonsin behöver justeras.

### Windows

Claude Code har ingen nyckelringsintegration på Windows: `claude login`
skriver i stället exakt samma `{"claudeAiOauth": {...}}`-post till en vanlig
fil, `%USERPROFILE%\.claude\.credentials.json`. Kör tjänsten på Windows och
den läser den filen — ingen dialog, ingen konfiguration, samma
förtroendegräns som nyckelringsläsningen på Macen. Nyckelringen och Claude
Desktops processtoken frågas inte alls där; `security` och `pgrep` finns
ändå inte.

Probelåset — maskinvida enprobe-garantin som håller 429-straffrutan borta —
finns kvar på Windows: `fcntl` saknas där, så låset tas med
`msvcrt.locking` i stället. Samma icke-blockerande grind, annat systemanrop.

Codex-halvan fungerar också. Kvotläsningen startar `codex app-server` och
läser dess stdout; det gjordes förut med `select.select`, som på Windows
bara tar sockets — aldrig pipes. Läsningen sker nu i en läsartråd med kö,
samma kod på alla plattformar.

Codex skrivbordsapp och Codex CLI är två olika installationsytor på Windows.
Store-appens `codex`-alias kan synas för `Get-Command` men ändå nekas när en
bakgrundsuppgift försöker starta det. Installera därför OpenAI:s fristående
CLI en gång i PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

Öppna därefter ett nytt PowerShell-fönster och verifiera `codex --version`.
VibePulse föredrar installerarens stabila användarsökväg
`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe` framför `PATH` och
ignorerar avsiktligt `WindowsApps`-alias. Kör
`python tools\vibepulse_setup.py doctor` efter installationen; ett grönt
`PASS Codex executable` betyder att samma körbara CLI kan användas av
setupverktyget och tokenserverns app-serverläsning.

Tillståndsfilerna (lås, probestatus, kvotcache, historik, max-spårare) bor
under `%LOCALAPPDATA%\VibePulse\` i stället för macOS
`~/Library/Application Support/VibePulse`. Sökvägarna fungerade bokstavligt
även förut — `Path.home()` löser ut — men lade ett `Library`-träd i
användarprofilen som ingenting annat på maskinen känner igen.

Autostart ingår via Task Scheduler. Kör från repots rot i PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1
```

Valfria visningskällor måste anges till den schemalagda tjänsten precis som
vid en manuell start. Repo-värdet är publikt; abonnemangskostnader gissas
aldrig:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 `
  -GithubRepo "owner/repository" `
  -ClaudePlan max5x -ClaudePlanCostUsd "100" `
  -CodexPlan pro -CodexPlanCostUsd "20"
```

GitHub-sidan och stjärnnotiserna behöver dessutom sina separata
`TK_GITHUB_*_ENABLED`-flaggor i panelens gitignorerade `secrets.h`.

Skriptet registrerar tjänsten för den inloggade användaren, startar den
direkt och startar om den vid fel. Det bakar inte in Claude/Codex- eller
detaljval i kommandoraden; samma sparade tokenserver-konfiguration används
som vid manuell start. En dold PowerShell-wrapper kör den verifierade
Python 3.11+-tolken och skriver stdout/stderr till
`%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log`; loggen roteras vid
cirka 5 MB med en `.old`-svans. Kontrollera hälsan med
`curl http://localhost:8737/`. Kontrollera installeraren utan att röra
Task Scheduler med `-ValidateOnly`. Avinstallera själva autostarten med:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 -Uninstall
```

Svar (kontraktet appen parsar, `components/app_tokens/tokens_parse.c`;
null = ärlig frånvaro, skärmen visar streck):

```json
{"v": 2, "dayTokens": 48231907, "dayTokensPerHour": 5120000,
 "daySessions": 4, "monthTokens": 612480233,
 "claudeSessionPct": 21.0, "claudeSessionResetMin": 80,
 "claudeWeekPct": 47.0, "claudeWeekResetMin": 850,
 "claudeWeekStale": false,
 "claudeModelWeekPct": 73.0, "claudeModelWeekResetMin": 850,
 "claudeModelWeekLabel": "FABLE · WEEK",
 "claudeModelWeekStale": false,
 "claudeWeekTodayDeltaPct": 6.0,
 "claudeModelWeekTodayDeltaPct": 3.0,
 "claudeSessionHourDeltaPct": 4.0,
 "codexSessionPct": null, "codexSessionResetMin": null,
 "codexWeekPct": 35.0, "codexWeekResetMin": 2210,
 "codexWeekStale": false,
 "codexWeekTodayDeltaPct": 2.0,
 "claudeForecastState": "at_reset",
 "claudeForecastPctAtReset": 85,
 "claudeForecastPaceFactor": 1.4,
 "claudeForecastAt": null, "claudeForecastOffsetMin": null,
 "codexForecastState": "collecting",
 "codexForecastPctAtReset": null,
 "codexForecastPaceFactor": null,
 "codexForecastAt": null, "codexForecastOffsetMin": null}
```

De nya delta- och prognosfälten är frivilliga för äldre skärmkod och `null`
när underlaget saknas. Prognosen blir först aktiv efter minst tre punkter,
90 minuters spann och en procents faktisk rörelse i samma resetcykel.

## Kvotcache och stale-kontrakt

Senaste auktoritativa Claude- och Codex-värden för generell vecka och
modellvecka sparas atomiskt i
`~/Library/Application Support/VibePulse/quota-cache.json`. Identiteterna i
filen är lokala SHA-256-värden; råa leverantörs-id:n, sessionssökvägar,
projekt, chattar och innehåll sparas inte. Sessions-/5h-fönstret cachelagras
inte.

- En lyckad aktuell observation har procent och absolut reset, skrivs till
  cachen och serveras med `*Stale: false`.
- När nästa schemalagda probe eller skanning har misslyckats (även när Codex
  bara gav en namngiven modellkvot) får det förra minnesvärdet inte fortsätta
  se live ut. En matchande, ännu ej utgången cachepost kan då serveras med
  `*Stale: true`.
- Vid exakt reset-tid är posten utgången. Då, eller utan cacheträff, är
  procent, reset och eventuell etikett `null` och `*Stale` är `false`.
- `ResetMin` räknas om från absolut reset vid varje svar, så ett cachevärdes
  återstående minuter fortsätter minska. Stale-värden skrivs inte till
  usagehistoriken och används inte för delta eller prognos.

Booleska `claudeWeekStale`, `claudeModelWeekStale` och `codexWeekStale` är
frivilliga tillägg i v2-kontraktet. Om procenten saknas är motsvarande stale
alltid `false`.

## Lokal usagehistorik

Tjänsten sparar historiken atomiskt i
`~/Library/Application Support/VibePulse/usage-history.json` på macOS och
under `%LOCALAPPDATA%\VibePulse\` på Windows. Högst en punkt
per leverantör, fönster och 15 minuter behålls, och allt äldre än åtta dagar
rensas. Varje punkt har exakt fem värden: tid, `claude`/`codex`, quotafönster,
procent och avrundad resetcykel. Promptar, svar, kommandon, projekt, filnamn,
modeller och tokeninnehåll kan inte skrivas till filen.

VECKOTAKT räknas med en utjämnad procentslope från högst de senaste 24
timmarna i den aktuella veckocykeln. Resultatet är antingen `collecting`,
`unavailable`, beräknad procent vid reset (`at_reset`) eller beräknad tid då
quotan tar slut (`exhausts`).

Installera den valfria lokala upptäckten i samma Pythonmiljö som tjänsten:

```sh
python3 -m pip install -r requirements-discovery.txt
```

Tjänsten annonserar då `_vibepulse._tcp.local` utan kvoter, projektdata eller
hemligheter. Aktuell firmware cachar en frisk Mac/PC och byter först efter ett
begränsat fel. Utan paketet startar samma stdlib-tjänst och använder den
kompilerade reservadressen exakt som tidigare.

Peka därför fortfarande skärmens fallback hit i reporotens `secrets.h`. På
macOS är Bonjour-namnet bäst; på Windows används en DHCP-reserverad LAN-adress:

```c
#define TK_TOKENS_URL "http://<datorns-host-eller-lan-ip>:8737/api/tokens"
```

Macens Bonjour-namn: `scutil --get LocalHostName` (lägg till `.local`).
Windows LAN-IP: `ipconfig`; reservera den valda IPv4-adressen i routern.

## Autostart via launchd

Install from the intended clean, durable checkout with the path-safe helper:

```sh
python3 tools/vibepulse_macos_service.py validate
python3 tools/vibepulse_macos_service.py install
```

`validate` is read-only. `install` resolves the current checkout and its
`.venv/bin/python`, writes the LaunchAgent atomically, preserves recognized
existing runtime arguments and environment values without printing them, and
uses `bootout` plus `bootstrap` so launchd cannot retain an older cached
command. It refuses foreign, symlinked, malformed, or unrecognized existing
plists. A short, bounded retry absorbs launchd's transient post-`bootout`
bootstrap race. If every new-service attempt fails, the previous plist is
restored and reloaded with the same bounded retry. Python 3.11+ and the exact
tokenserver source must exist before it changes anything.

Manual installation remains available for unusual layouts:

```sh
cp se.torget.tokenserver.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/se.torget.tokenserver.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/se.torget.tokenserver.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/se.torget.tokenserver.plist
```

Mallplisten antar att repot bor i `~/Torget`, att repots Python 3.11+-miljö finns
i `.venv` och att användaren är `niclasvestlund` — redigera sökvägarna
annars. Med krypterat interaktionsrelä aktiverat ska den miljön även ha
`requirements-interaction-relay.txt` installerad. Loggen hamnar i
`~/Library/Logs/torget-tokenserver.log` (syns i Konsol-appen, överlever
omstart; servern roterar den själv vid start om den vuxit förbi ~5 MB, med
svansen bevarad i `.old`). Raderna har tidsstämplar och loggar övergångar,
inte tillstånd: en frisk vecka är några rader, inte tusen.

Använd en ren, beständig checkout som inte ska raderas när en PR är klar.
`ProgramArguments[0]` (Python) och `WorkingDirectory` måste peka på samma
checkout. Om någon plist-rad ändras räcker inte `kickstart`: launchd behåller
den redan inlästa konfigurationen. Kör `bootout` + `bootstrap` enligt ovan.
Bevara privata argument och reläinställningar när bara sökvägen flyttas.

Efter installation eller flytt ska tre lager peka på samma källa:

1. `python3 tools/vibepulse_setup.py doctor` ska godkänna Codex-plugin, MCP
   och tokenserver. Hook-trust granskas fortfarande manuellt i `/hooks`.
2. `python3 tools/tokenserver/smoke.py --base-url http://127.0.0.1:8737`
   ska rapportera den väntade `rev`, matchande källfingeravtryck och noll fel.
   Ange den faktiskt konfigurerade porten om den inte är 8737.
3. Starta en ny Codex-task efter att plugin/MCP har flyttats, så att den nya
   processen verkligen laddas. Tystnad eller en gammal task är inte bevis.

En panel som startats om av den begränsade HTTP-stall-vakten skickar den fasta
lokala headern `X-VibePulse-Recovery-Boot: http-stall-v1`. Efter två bekräftade
LAN-pollar visar `GET /` endast booleanen
`interactions.panel.httpStallRecoveryBoot`; ingen paneladress, firmwaretext,
användare eller kvot följer med. Doctor och nästa Codex-start kan därför se en
självläkning även på väggström utan seriell kabel. Markören är evidens om
omstarten, inte ensam ett fysiskt PASS.

Röktestets totalsiffror för tracebacks och starter omfattar bevarad historik.
Efter en reparation ska även tidsstämplarna kontrolleras: exakt en ny start och
inga nya traceback/error-rader efter omladdningen är det friska resultatet.

Snabbaste hälsokollen är röktestet — kamrutinens steg 1–4 som ett kommando:

```
python3 tools/tokenserver/smoke.py
```

## Ärlighetsnoter

- Tokens = in + ut + cacheskrivning + cacheläsning, dedupade på
  message.id + requestId (återupptagna sessioner dubbelräknas inte).
- `dayTokensPerHour` är senaste timmens faktiska förbrukning — 0 betyder
  paus, och då låter skärmen bli att ticka. Inga hittade takter.
- Codex-procenten kommer primärt från Codex egen aktuella
  `account/rateLimits/read`-snapshot. Den generella `codex`-bucketen hålls
  åtskild från namngivna modellkvoter som Spark. Rollout-loggar används bara
  som fallback; ett passerat `resets_at` eller en fallbackskanning utan
  generell observation räknas som källfel och följer stale-kontraktet ovan.
  Claude-proben kostar en tom förfrågan var 240:e sekund — försumbart mot
  fönstren den mäter.
- Är datorn av visar skärmen streck efter två minuter (stale), inte gamla
  siffror som låtsas vara färska. Det är rätt beteende, inte ett fel.
