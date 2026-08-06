# Tokenmätaren som agentmonitor

**Datum:** 2026-08-06

**Status:** Godkänd med plattformsgranskningens fysiska AMOLED-grind

**Målplattform:** Waveshare ESP32-S3-Touch-AMOLED-2.16, 480 × 480, LVGL 9.5

## Sammanfattning

Tokenmätaren ska utvecklas från en stilla användningsmätare till en fysisk,
avläsbar agentmonitor för Claude Code och Codex. När ingen agent arbetar visas
de vanliga tokenvyerna. När en agent börjar arbeta tar en särskild helskärmsvy
över. Den använder ett stort pixelhusdjur, ett enda mycket stort tillståndsord
och högst en kort aktivitetsrad. Vid väntan, avslut eller fel byter samma vy
tydligt tillstånd och kan spela en kort lokal ljudsignal eller röstfras.

Riktningen är medvetet **less is more**. På håll ska den viktigaste frågan gå
att besvara omedelbart: jobbar agenten, väntar den på mig, eller är den klar?
Procentvärdet finns kvar men är sekundärt medan arbete pågår.

## Mål

- Visa agentens verkliga tillstånd läsbart på flera meters avstånd.
- Ge Claude och Codex egna, igenkännbara pixelkaraktärer.
- Behålla Tokenmätarens tydliga procent och nuvarande användningsvyer.
- Visa en säker och kort beskrivning av aktiviteten när den kan härledas.
- Meddela en avslutad eller blockerad uppgift en gång, även om användaren gått
  från skrivbordet.
- Vara följsam på ESP32-S3 utan helskärmsanimationer eller onödig minneslast.
- Aldrig påstå att en agent är klar enbart för att loggen varit tyst.

## Icke-mål för första versionen

- Godkänna kommandon eller ge agenten nya instruktioner från skärmen.
- Visa råa promptar, terminalsvar, filinnehåll eller fullständiga felmeddelanden.
- Strömma text eller terminalutdata till skärmen.
- Bygga en röstassistent eller använda mikrofonen.
- Göra ett generellt ljud- eller notifikationsbibliotek innan en andra app
  faktiskt behöver det.
- Bygga om Solelkollens designsystem eller Tokenmätarens samtliga statistikvyer.

## Upplevelseprinciper

1. **Tillstånd före dekoration.** En blick ska räcka. Husdjuret stödjer ordet;
   färg får aldrig ensam bära betydelsen.
2. **En skärm, ett budskap.** Ingen dashboard medan en agent arbetar.
3. **Rörelse betyder liv.** Bara det aktiva elementet animeras. All annan text
   står still.
4. **Sanningsenligt före snabbt.** `KLAR` kräver en uttrycklig avslutshändelse.
   Osäker status visas aldrig som arbete eller avslut.
5. **Sekretess som standard.** Enheten visar projekt och en kontrollerad
   aktivitetskategori, inte användarens råa innehåll.
6. **AMOLED på riktigt.** Äkta svart bakgrund, få ljusa pixlar, fortsatt
   pixeldrift och dämpning av långlivade slutsidor.

## Tillståndsmodell

Varje leverantör har ett eget tillstånd:

| Tillstånd | Betydelse | Helskärmsbudskap | Färg |
|---|---|---|---|
| `idle` | Ingen aktiv eller kvarstående händelse | Vanlig användningsvy | Befintlig accent |
| `working` | Agenten arbetar uttryckligen | `JOBBAR` | Vitt husdjur, leverantörsaccent |
| `waiting` | Agenten behöver användarens åtgärd | `VÄNTAR` | Amber |
| `done` | Uppgiften avslutades normalt | `KLAR` | Claude korall / Codex blåviolett |
| `error` | Uppgiften avslutades med fel | `FEL` | Röd |
| `unknown` | Uppgifterna är för gamla eller motsägelsefulla | Ingen helskärmsstatus | Ingen statusfärg |

Prioritet när flera tillstånd finns samtidigt är `waiting`, `error`,
`working`, `done`, `idle`. Vid samma prioritet visas den senast uppdaterade
agenten. Två små leverantörspunkter högst upp visar om båda agenterna är
aktiva; ett tryck på dem växlar agent utan att skapa en ny vyhierarki.

`waiting`, `done` och `error` ligger kvar tills användaren trycker bort dem
eller samma agent börjar en ny uppgift. Efter 15 minuter dämpas panelen men
budskapet ligger kvar, så att ett avslut fortfarande syns när användaren
kommer tillbaka. Plattformens pixeldrift fortsätter som vanligt.
Under `working` anropar appen `torget_keep_awake()` vid varje giltig
statusuppdatering. Slutsidorna gör inte det och dämpas därför naturligt av
plattformens befintliga nattlogik.

### Övergångar

- Start- eller verktygshändelse → `working`.
- Ny aktivitetskategori under samma uppgift → fortsatt `working`, ny rad.
- Uttryckligt tillstånd för tillståndsfråga, blockering eller väntan på input
  → `waiting`.
- Uttryckligt normalt slut → `done`.
- Uttryckligt misslyckat slut → `error`.
- Ny uppgift från `done`, `waiting` eller `error` → `working`.
- Tryck på en kvarstående slutsida → `idle` och ordinarie Tokenmätare.
- För gammal arbetsstatus utan bevisad process eller avslutshändelse →
  `unknown`, aldrig `done`.

## Skärmar

### 1. Ordinarie Tokenmätare

De tre nuvarande vyerna för Claude-tak, Codex-tak och Claude-volym behålls i
första leveransen. Deras procent är fortsatt stora och tydliga. Agentmonitorn
läggs ovanpå appens tileview och förändrar därför inte befintlig svepning,
hämtning eller formatering. För Claude Max ligger Fable-fönstret kvar som en
egen rad; det får aldrig räknas bort eller slås ihop med veckofönstret.

### 2. Arbetar

Skärmen innehåller, uppifrån och ned:

- en kompakt rad med leverantör och projektnamn, till exempel
  `CLAUDE CODE · TORGET`;
- ett stort centrerat pixelhusdjur;
- tillståndsordet `JOBBAR`, skärmens största text efter husdjuret;
- tre små aktivitetsrutor som går vänster–mitten–höger;
- en valfri, kontrollerad aktivitetsrad, exempelvis `KÖR TESTER`;
- den begränsning som ligger närmast taket i en liten bottenrad, exempelvis
  `73,0 % FABLE`, med en tunn mätare. Claude jämför sessionen, veckan och
  Fable-fönstret; Codex jämför sina tillgängliga fönster. Null-fönster
  ignoreras. Procentsatserna summeras eller medelvärdesbildas aldrig.

Layouten låses till en 24 px säker kant. Toppraden ligger kring y=24–54,
husdjuret i en högst 180 × 180 px-yta kring y=66–240, huvudordet kring
y=252–322, aktivitet kring y=348 och procent/mätare kring y=408–450.
Huvudordet använder ett nytt, hårt beskuret versalfontläge omkring 64 px;
övriga rader återanvänder IBM Plex i befintliga mindre storlekar. Den exakta
petstorleken får bara finjusteras under fysisk granskning, inte genom att
lägga till fler UI-element.

Aktivitetsraden får vara högst 24 tecken och endast använda enhetens fasta
kategorier. Saknas säker aktivitet tas raden bort; den ersätts inte av en
gissning.

### 3. Väntar

Samma komposition, men husdjuret är amber och står stilla med en långsam
blinkning. Huvudordet är `VÄNTAR`. Den lilla raden kan vara
`BEHÖVER DITT GODKÄNNANDE` eller `BEHÖVER ETT SVAR`. Ingen knapp för själva
godkännandet finns i version ett; ett tryck kvitterar bara skärmens lokala
notis och återgår till Tokenmätaren.

### 4. Klar

Vid normalt slut fylls husdjuret uppifrån och ned med leverantörens färg på
cirka 600 ms. Det gör en enda liten studs och går sedan till en lugn,
nästan stilla blinkning. Huvudordet är `KLAR` och underraden
`VÄNTAR PÅ DIG`. Procentraden kan ligga diskret kvar längst ned men får inte
konkurrera med avslutet.

### 5. Fel

Husdjuret blir rött och huvudordet blir `FEL`. Underraden använder en
kontrollerad formulering som `KUNDE INTE FORTSÄTTA`. Råa fel och
terminalutdata skickas inte till skärmen.

## Pixelkaraktärer och animation

### Claude

Claude använder den godkända fyrkantiga pixelkatten/krabban som en 1-bitars
mask. Samma mask kan färgsättas vitt, korall, amber eller rött utan separata
helbilder. Aktiv loop:

1. neutral;
2. kroppen ett par pixlar upp;
3. ögonen blinkar;
4. kroppen tillbaka.

Loopen går i 6–8 bildrutor per sekund. Slutsidor animerar bara en sällsynt
blinkning.

### Codex

Codex använder det blåvioletta molnet som en begränsad palettbild. Tecknen
`>` och `_` ligger i separata små lager så att de kan blinka och förflyttas
utan att hela molnet ritas om. Aktiv loop är en mycket liten andning i
molnets siluett plus en terminalblinkning.

### Rörelsebudget

- Ingen animation får kräva en hel 480 × 480-buffer.
- Petens smutsiga yta ska hållas kring högst 220 × 220 px.
- Bara husdjur och tre aktivitetsrutor uppdateras kontinuerligt.
- Mål: 6–8 fps under arbete, högst enstaka bildbyte i slutlägen.
- Ingen blinkning snabbare än 3 Hz.
- Färdiga assets lagras i flash; inga PNG-avkodningar eller filsystem krävs
  under körning.

## Vad agenten arbetar med

Tokenservern får härleda en aktivitetskod från logghändelser och verktygsnamn.
Enheten översätter koden till svensk displaytext:

| Kod | Visas |
|---|---|
| `thinking` | TÄNKER |
| `reading` | LÄSER KOD |
| `editing` | ÄNDRAR FILER |
| `searching` | SÖKER I PROJEKTET |
| `running` | KÖR KOMMANDO |
| `testing` | KÖR TESTER |
| `building` | BYGGER PROJEKTET |
| `waiting_input` | BEHÖVER ETT SVAR |
| `waiting_approval` | BEHÖVER DITT GODKÄNNANDE |

Projektnamn får härledas från arbetskatalogen eller en explicit säker titel.
Rå prompt, kommandorad, filinnehåll och assistentens svar ingår aldrig i
statuskontraktet.

## Serverkontrakt

Tokenservern får en snabb separat endpoint:
`GET /api/agent-status`. Den svarar från minne och får inte skanna hela
logghistoriken i HTTP-anropet.

```json
{
  "v": 1,
  "seq": 184,
  "agents": {
    "claude": {
      "task_id": "local-opaque-id",
      "event_id": "stable-opaque-event-id",
      "state": "working",
      "project": "Torget",
      "activity": "testing",
      "updated_ms": 420
    },
    "codex": {
      "task_id": null,
      "event_id": null,
      "state": "idle",
      "project": null,
      "activity": null,
      "updated_ms": 0
    }
  }
}
```

`seq` ökar vid varje verklig statusändring och versionsmärker hela paketet.
`event_id` är stabilt över en tokenserveromstart och ändras vid varje
användarsynlig tillståndsövergång; enheten använder det för att deduplicera
ljud. `task_id` och `event_id` är opaka och visas aldrig. Strängar begränsas
i längd, kontrolltecken tas bort och okända enumvärden behandlas som
`unknown`.

En inkrementell watcher håller offset per aktiv Claude-JSONL och
Codex-rollout. Den läser bara nytillkomna poster. Status-endpointen pollas av
enheten ungefär varje sekund; befintliga tokenvärden fortsätter hämtas i sin
långsammare kadens. En serveromstart rekonstruerar senaste säkra status men
återupplivar inte gammalt `working` utan färsk händelse eller bevisad process.

`working` har en två minuter lång lease som förnyas av nya agenthändelser.
Om inga händelser kommer och tokenservern inte kan bevisa att processen lever
övergår den till `unknown`; den övergår aldrig automatiskt till `done`.
Terminala lägen har ingen sådan lease och ligger kvar tills de kvitteras eller
ersätts av en ny uppgift.

## Lokal ljudåterkoppling

Kortets befintliga ES8311-högtalarväg används med BSP:ns standardformat:
22 050 Hz, mono, 16-bitars PCM. Korta ljud och röstfraser bäddas in som
read-only-data i appens flash eftersom nuvarande partitionstabell saknar ett
assetfilsystem.

Första versionen har högst tre korta notifieringar:

- klart: mjuk ton + `Claude är klar och väntar på dig` eller
  `Codex är klar och väntar på dig`;
- väntar: två lätta toner + `Claude väntar på dig`;
- fel: låg ton, utan uppläsning av felet.

Ljud spelas bara en gång per nytt `event_id`. Senast notifierade event per
agent lagras i NVS, så en gammal händelse spelas inte på nytt efter omstart
av enheten eller tokenservern. En separat liten FreeRTOS-kö matar ljudet så
att rendering och nätverk aldrig blockeras. Volymen är låg som standard. En
44 × 44 px touchyta för en diskret högtalarglyf längst till höger i toppraden
slår av eller på ljudet; valet lagras i NVS och överlever omstart. Glyfen är
det enda extra reglaget på statusvyn.

## Programstruktur

Ansvarsfördelningen följer Torgets befintliga regler:

- `tools/tokenserver/`: loggwatcher, statusmaskin, sanering och endpoint;
- `components/app_tokens/`: statusparser, statuspollning, overlay-UI,
  pet-assets och appspecifikt ljud;
- `platform/`: oförändrad, utom om fysisk verifiering visar att en redan
  generell värdfunktion saknas;
- `sim/`: tangentstyrda fixtures för varje status, inte specialritat UI;
- `sim-fixtures/`: syntetiska, sekretessfria statuspaket.

Ett möjligt internt filsnitt är `agent_status.c/.h`, `agent_monitor.c/.h`
och `agent_audio.c/.h` i appkomponenten samt `agent_status.py` bredvid
tokenservern. Slutlig uppdelning avgörs mot befintliga beroenden i
implementationsplanen; inga bibliotek skördas i förväg.

## Fel- och bortkopplingsbeteende

- Ingen första data: ordinarie Tokenmätare visar streck enligt befintlig
  ärlighetsinvariant; agentoverlay visas inte.
- Status-endpoint nere: behåll inte `JOBBAR` obegränsat. Efter statusens
  tvåminuterslease blir den `unknown` och overlayn lämnas.
- Trasig JSON eller okänd version: avvisa hela statuspaketet, behåll senaste
  säkra värde endast inom tvåminutersleasen och logga orsaken.
- Tokenendpoint nere men agentstatus fungerar: statusvyn fungerar; den lilla
  procentraden visar streck.
- Agentstatus nere men tokenendpoint fungerar: nuvarande Tokenmätare fungerar
  precis som idag.
- Ljudfel: visuellt tillstånd får aldrig påverkas.

## Verifiering och acceptanskriterier

### Server och parser

- Testa Claude- och Codex-händelser med syntetiska fixtures.
- Verifiera samtliga tillståndsövergångar och prioritetsregeln för två agenter.
- Verifiera att tystnad aldrig ger `done`.
- Verifiera längdgränser, kontrolltecken, okända enumvärden och versionsfel.
- Verifiera att endpointen svarar från minne och inte gör en full loggskanning.

### Simulator

- En tangent cyklar `idle → working → waiting → done → error → unknown`.
- Automatisk BMP-dump finns för Claude och Codex i minst `working`, `waiting`
  och `done`.
- Skärmbilder granskas i faktisk 480 × 480-storlek och nedskalade till ungefär
  den visuella storleken på den fysiska skärmen.
- Befintliga tre tokenvyer, launcher, svepning och KEY3-beteende regressionsprovas.

### Fysisk AMOLED

- Husdjur, huvudord och sekundärrad ska kunna läsas på 1, 2 och 3 meters håll.
- Aktiv animation ska vara jämn utan att touch, rotation eller nätverk tappar
  respons.
- LVGL-heap följs före, under och efter minst 30 minuters animation; ingen
  växande allokering accepteras.
- Flashmarginal, RAM och faktisk uppdaterad yta mäts före godkännande.
- Ljud testas på låg volym och får inte spela två gånger för samma `event_id`.
- `done` ska fortfarande synas efter 15 minuter i dämpat läge och pixeldrift
  ska vara aktiv.

## Leveransordning

**Obligatoriskt före steg 1:** läs hela
[`docs/agentmonitor-granskning.md`](../../agentmonitor-granskning.md). Dess
hårdvarukrav för 24-radsflush, flashlagrade bildmasker, långlivad HTTP-klient,
lokal lease, overlayintegration, fontplacering och ljudets DMA-minne är en del
av denna specifikation och ska finnas med i implementationsplanen.

1. Statuskontrakt och syntetiska fixtures.
2. Inkrementell statuswatcher i tokenservern med tester.
3. Parser och en statisk overlay i simulatorn.
4. **Första fysiska AMOLED-grinden:** visa den statiska overlayn på glaset och
   justera storlek, kontrast, radbrytning och faktisk läsbarhet innan någon
   animationskod skrivs.
5. Claude- och Codex-animationer samt samtliga färgtillstånd.
6. Separat snabb statuspollning och hela tillståndsmaskinen.
7. Andra fysiska AMOLED-grinden: verifiera fps, dirty areas, heap och
   30 minuters stabilitet med animation och polling aktiva.
8. Lokal ljudsignal/röst med deduplicering.
9. Full regression, dokumentation och slutlig fysisk verifiering.

Först efter att denna version fungerar stabilt tas nästa steg: säker parning,
autentisering och eventuella godkännanden från skärmen. Den funktionen ska
designas som ett eget säkerhetsflöde och får inte smygas in i den läsande
agentmonitorn.
