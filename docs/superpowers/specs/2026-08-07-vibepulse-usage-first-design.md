# VibePulse usage-first redesign

**Datum:** 2026-08-07

**Status:** Godkänd design, inväntar granskning av denna samlade specifikation

**Visuell riktning:** A – Usage-korten
**Ersätter:** Agentmonitorns helskärmslägen där `JOBBAR`, `VÄNTAR` eller
`KLAR` konkurrerar med quota. Säkerhets-, lease-, ljud- och
integritetskraven i den befintliga agentmonitor-specen gäller fortsatt.

## Produktmål

VibePulse ska först svara på hur mycket quota som har förbrukats, när den
återställs och om nuvarande takt räcker för att använda veckans quota. Det ska
samtidigt gå att se på håll om Claude Code eller Codex fortfarande arbetar och
vilken typ av säker aktivitet som pågår.

Skärmen är en fysisk 480 × 480-AMOLED. Less is more: procenten är hjälten,
animationen är lågmäld och inga detaljer får göra quota svårare att läsa.

## Gemensamt visuellt system

- Äkta svart AMOLED-bakgrund och mörka, tunna usage-kort.
- IBM Plex och befintliga riktiga pixel-/ikonassets.
- Inga gröna, lila eller andra färgade etikettpiller.
- Quota-etiketter är neutral, liten versal text.
- Claude använder en varm korallaccent i progress och aktivitet.
- Codex använder en blå accent i progress och aktivitet.
- Färg är aldrig enda betydelsebäraren; text och siffror finns alltid.
- Inga helskärmsblinkningar när status, provider eller quota ändras.
- Inga nya dekorationer eller textrader läggs till utan att något annat tas
  bort.

## Gemensam överkant

Överkanten är 52 px hög och helt svart:

`[V.-ikon]  CLAUDE                         FABLE 5 / XHIGH`

eller:

`[V.-ikon]  CODEX                     GPT-5.6 SOL / XHIGH`

- Den riktiga `V.`-ikonen från Torget-launchern identifierar VibePulse.
- `CLAUDE` eller `CODEX` identifierar providern utan att förlita sig på färg.
- Modell och effort ligger högerställda på två kompakta rader.
- Saknade modell- eller effortfält utelämnas; UI:t gissar aldrig.
- Långa, säkra värden kortas inom sin yta och får inte flytta korten.
- VibePulse-namnet och den stora appidentiteten används i launchern/startläget,
  inte som en konkurrerande rubrik på varje usage-sida.

## Claude-sidan

Claude har två usage-kort.

### Övre kortet: aktuell modellgräns

När Claude faktiskt rapporterar en separat modellgräns är den fast överst,
med den rapporterade etiketten, exempelvis:

- `FABLE · VECKA`
- `OPUS · VECKA`
- `SONNET · VECKA`

Etiketten får inte härledas enbart från aktiv modell. Om tjänsten inte har en
separat modellgräns visas ingen påhittad Opus-/Fable-quota; då blir
`ALLA · VECKA` det fasta överkortet.

### Undre kortet: lugn rotation

När båda värdena finns växlar undre kortet var sjunde sekund mellan:

- `ALLA · VECKA`
- `5 TIMMAR`

Endast innehållet i det undre kortet tonas eller förflyttas några pixlar.
Överkort, överkant, aktivitet och hela skärmen står still. Två små indikatorer
visar vilket av de två lägena som visas. Om bara ett värde finns roterar inget.

### Kortens innehåll

Varje kort visar:

- stor förbrukad procent;
- quota-fönstrets namn som neutral text;
- reset som både kort återstående tid och exakt lokal dag/tid när den finns;
- rak progressbar;
- förändring över tid när historiken räcker.

Veckokort visar exempelvis `+12% IDAG`. Progressbaren delar totalen i en
dämpad del från tidigare dagar och en ljus del för dagens nya
procentenheter. 5-timmarskortet kan på motsvarande sätt visa förändring under
senaste timmen. Om quota återställs under dagen byts veckotexten till
`+… SEDAN RESET` så att en negativ eller missvisande dagsförändring aldrig
visas.

## Codex-sidan

Codex visar `ALLA · VECKA` som ett enda större huvudkort. Sessions-/5h-quota
visas inte på Codex huvudskärm. Den frigjorda ytan används till större procent,
längre reset-rad och luft.

Codex-kortet använder samma dagsförändring och delade progressbar som
Claude-kortens veckofönster. Överkanten visar verklig Codex-modell och effort
när de finns.

## Centrerad aktivitetsrad

Längst ned ligger en 66 px hög, centrerad grupp:

`[provider-pet]  ÄNDRAR FILER / PROJEKT · TORGET  [pulsstaplar]`

Raden visar den säkraste tillgängliga aktivitetskategorin i stället för det
generiska och visuellt tunga `JOBBAR`:

- `TÄNKER`
- `LÄSER KOD`
- `ÄNDRAR FILER`
- `SÖKER I PROJEKTET`
- `KÖR KOMMANDO`
- `KÖR TESTER`
- `BYGGER PROJEKTET`
- `BEHÖVER ETT SVAR`
- `BEHÖVER GODKÄNNAN`
- `VÄNTAR PÅ DIG`
- `FEL`

Projekt visas endast när det finns ett sanerat projektnamn. Peten får röra sig
1–2 px och tre små staplar pulserar medan agenten arbetar. Test-/byggaktivitet
kan ha något snabbare puls. Vid väntan, klart eller fel stannar rörelsen och
texten ändras. Usage-korten ligger kvar i alla agentlägen.

Promptar, resonemang, terminalkommandon, filnamn och meddelandetext visas
aldrig. Aktivitetsraden bygger endast på sanerade kategorier som redan kan
klassificeras lokalt.

## VECKOTAKT – gemensam prognossida

En separat sida visar Claude och Codex tillsammans, baserat på respektive
providers övergripande veckofönster. Syftet är “tokenmaxing”: att se om
nuvarande takt använder quotan lagom till reset.

För varje provider visas nuvarande veckoprocent och ett av följande besked:

- `85% VID RESET` + `ÖKA 1,4× FÖR ATT MAXA` när takten är för låg;
- `QUOTAN TAR SLUT LÖR 05:00` + `9 H TIDIGT` när takten når 100 % före reset;
- `SAMLAR TAKT` när historiken ännu inte räcker;
- `PROGNOS SAKNAS` när quota eller reset saknas/stale.

Formler:

- faktisk takt = förändring i veckoprocent / förfluten tid;
- nödvändig takt = procent kvar / tid till reset;
- maxningsfaktor = nödvändig takt / faktisk takt;
- väntad sluttid = nu + procent kvar / faktisk takt.

Prognosen använder quota-procent, inte råa tokenantal, eftersom modeller,
cache och effort belastar providerquotan olika.

## Historik och ärlighet

Mac-tjänsten sparar en liten, lokal och begränsad historik med högst:

- timestamp;
- provider och quota-fönster;
- förbrukad procent;
- reset-timestamp.

Den sparar inga promptar, projektdetaljer eller tokeninnehåll. En giltig punkt
tas högst var 15:e minut och historiken behålls i åtta dagar. Punkter från
olika reset-cykler blandas aldrig.

`+ IDAG` kräver en giltig baslinje från samma lokala kalenderdygn och samma
reset-cykel. Prognos kräver minst tre giltiga punkter över minst 90 minuter och
minst en procentenhets verklig förändring. Takten jämnas över de senaste
24 timmarna så att en kort burst inte ger en orimlig sluttid. Nolltakt visas
som `INGEN TAKT`, aldrig som oändlig faktor.

Historiken ska skrivas atomiskt, vara storleksbegränsad och tåla omstart eller
en halvskriven sista uppdatering.

## Navigation och skärmordning

Sidorna ligger i VibePulse och kan nås med lugn horisontell swipe:

1. Claude usage.
2. Codex usage.
3. VECKOTAKT.
4. Befintlig volymvy.

Aktiv agent kan välja initial provider enligt den befintliga prioritetspolicyn,
men användarens manuella swipe ska inte omedelbart ryckas tillbaka. Inget
agentläge ersätter usage-sidan med en helskärmsnotis. Långtryck öppnar fortsatt
Torget-launchern.

## Datakontrakt

`/api/tokens` fortsätter leverera befintliga quota-fönster och kompletteras
bakåtkompatibelt med valfria platta fält för presentation och prognos:

- Claude 5h/session + reset;
- Claude alla modeller/vecka + reset;
- Claude separat modell/vecka + reset + sanerad modellgränsetikett;
- Codex vecka + reset;
- dagsdelta för Claude modell/vecka, Claude alla/vecka och Codex vecka;
- timdelta för Claude 5h;
- prognosstatus per provider (`collecting`, `at_reset`, `exhausts` eller
  `unavailable`), plus relevanta värden för prognosprocent, sluttid,
  tidsskillnad mot reset och maxningsfaktor.

Alla prognosvärden beräknas på Macen. ESP32:n presenterar kontraktet och ska
inte själv lagra en veckas historik eller försöka regressionsberäkna takten.

Tjänsten måste behålla modellgränsens verkliga namn från den rapporterade
headern. Ett generiskt `modelPct` får inte automatiskt märkas `FABLE`.

Agentstatuskontraktet kompletteras med två valfria, sanerade fält:

- `model`, högst 24 UTF-8-byte;
- `effort`, högst 12 UTF-8-byte.

Codex hämtar dem från den senaste relevanta `turn_context` för samma turn.
Claude hämtar modell från senaste relevanta assistant-/sessionspost och effort
från sessionens effortfält. Endast kända presentationsnamn normaliseras, till
exempel `gpt-5.6-sol` till `GPT-5.6 SOL`; okända säkra värden kortas. Saknade
värden utelämnas i stället för att visas som `UNKNOWN`.

Ett fel i modell, effort, aktivitetskategori eller prognos får aldrig
underkänna ett i övrigt giltigt usage-svar.

## Fel- och tomlägen

- Saknad quota visas som `–` och `QUOTA SAKNAS`; progressbaren är tom.
- Saknad reset döljer reset-raden utan att flytta procentens baslinje.
- Saknad modellgräns skapar aldrig ett tomt kort eller en påhittad etikett.
- Senast godkända quota får stå kvar vid ett tillfälligt nätfel enligt
  befintlig nätpolicy.
- För gammal agentstatus blir inaktiv enligt tvåminutersleasen; usage ligger
  kvar.
- Saknad historik påverkar endast delta/prognos, aldrig aktuella quota-värden.
- Efter reset rensas den berörda beräkningsbaslinjen utan dramatisk animation.

## Leveransordning

1. Kontrakt och tester för modell/effort, verklig modellgränsetikett och
   historik/prognos.
2. Statisk 480 × 480-layout för Claude, Codex och aktivitetsrad.
3. Simulatorjämförelse mot de godkända målmockuperna.
4. **Fysisk AMOLED-titt direkt efter den statiska layouten.** Typstorlek,
   kortmarginaler, appikon, svartnivå och fysisk läsbarhet justeras här.
5. Lugn kortrotation, pet-/pulsanimation och statusväxling.
6. Dagsdelta och delad progressbar.
7. VECKOTAKT med persistent historik och tomlägen.
8. Targetbuild, flash, bootlogg och slutlig fysisk kontroll.

Den fysiska titten sker alltså före animation och prognos, inte sist.

## Verifiering

1. Python-fixtures för Claude/Codex med och utan modell, effort och
   modellgränsetikett.
2. Integritetstest som bevisar att prompt, resonemang, kommandon, filnamn och
   meddelandetext inte når snapshot, historik eller endpoint.
3. Historiktest för midnatt, reset mitt på dagen, omstart, halvskriven fil,
   stale data och max åtta dagar.
4. Prognostest för för låg takt, tidig exhaustion, nolltakt och otillräcklig
   historik.
5. C-parsertester för frivilliga, saknade, för långa och okända fält.
6. Usage-policytest för Claude modellgräns + roterande vecka/5h, Claude utan
   modellgräns och Codex endast vecka.
7. Simulatorbilder vid exakt 480 × 480 för båda providers, alla
   aktivitetslägen, båda Claude-rotationerna och VECKOTAKT.
8. Fysisk AMOLED-kontroll efter statisk layout och efter full funktion.
9. Targetbuild, flash och bootlogg utan omstarter eller checksummefel.

## Klart när

- VibePulse känns som ett tydligt mätinstrument, inte en generisk
  AI-dashboard.
- Den riktiga `V.`-ikonen, provider och modell/effort är tydliga i toppen.
- Usage är visuellt dominant i alla agentlägen.
- Claude visar verklig modellgräns samt lugnt roterande vecka/5h utan
  helskärmsblinkning.
- Codex visar en luftig veckovy utan 5h-quota.
- Veckokort visar dagens verkliga förändring när historiken räcker.
- Den centrerade aktivitetsraden visar verklig säker aktivitet och projekt.
- VECKOTAKT visar båda providers och ger en ärlig maxningsprognos.
- Saknade data utelämnas eller markeras ärligt; inget värde eller namn hittas
  på.
- Simulator och fysisk panel är läsbara utan överlapp, scrollbars eller
  oavsiktligt vita ytor.
