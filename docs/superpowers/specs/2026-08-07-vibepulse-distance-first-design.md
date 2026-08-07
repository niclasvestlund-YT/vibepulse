# VibePulse distance-first AMOLED redesign

**Datum:** 2026-08-07
**Status:** Visuell riktning godkänd; denna skrivna specifikation inväntar
användarens granskning före implementation.

**Ersätter:** De visuella layout-, rotations- och navigationsdelarna i
`2026-08-07-vibepulse-usage-first-design.md`. Dataintegritet, historik,
prognos och felhantering från den specifikationen gäller fortsatt. Klarsignal,
ljud, eventkö och kvittens från
`2026-08-07-vibepulse-completion-beacon-design.md` gäller fortsatt där de inte
uttryckligen ändras nedan.

## Beslut

VibePulse byggs som ett stabilt mätinstrument för en fysisk 480 × 480-AMOLED,
inte som en miniatyriserad dashboard. Den normala huvudvyn visar en provider
och en quota i taget med en mycket stor procent. En separat översikt visar
Claude och Codex samtidigt. Detaljer nås manuellt; inget usage-innehåll roterar
automatiskt.

Den valda riktningen kombinerar den stora, lugna hierarkin från den första
godkända 480 × 480-skissen med tvåprovidersöversikten från den tredje skissen.
Den andra skissens tvåbandssystem används endast på en manuell detaljsida.

## Produktmål och framgångskriterier

Huvudskärmen ska på en blick svara på:

1. Vilken provider visas?
2. Hur stor del av den viktiga quotan är förbrukad?
3. När återställs quotan?
4. Arbetar providern fortfarande eller behöver den användaren?

På den fysiska panelens cirka 39 × 39 mm aktiva yta ska provider och
huvudprocent kunna identifieras från 1–2 meters avstånd. Reset och aktivitet
ska vara tydliga på armlängds avstånd. Ingen sekundär text får konkurrera med
procenten.

## Enhetlig quota-semantik

Alla progressbarer och huvudprocent räknar upp från noll och betyder
**förbrukad quota**.

- Claude-värden visas som rapporterad förbrukning.
- Codex rapporterar återstående andel. VibePulse beräknar
  `used = clamp(100 - remaining, 0, 100)` innan presentation. Exempel:
  `43% kvar` blir `57% USED`.
- Progressbarens färgade del blir längre när mer quota har förbrukats.
- `USED` står i quotaetiketten eller bredvid huvudprocenten men dupliceras
  aldrig i flera rader.
- Saknat eller ogiltigt värde visas som `–` och `USAGE UNAVAILABLE`; ingen
  omräknad siffra hittas på.

UI-språket är engelska. Providerernas vedertagna quota-namn bevaras:

- `5-HOUR LIMIT`
- `WEEKLY · ALL MODELS`
- `WEEKLY · FABLE` eller den verkliga rapporterade modellgränsen
- `WEEKLY` för Codex

## Visuellt system

### Färg

- Bakgrund: äkta AMOLED-svart `#000000`.
- Primär text: `#FFFFFF`.
- Sekundär text: minst `#ABB1BA`; kritisk information använder inte den
  mörkare muted-färgen.
- Track: `#303238`.
- Claude-accent: `#D97757`.
- Codex UI-accent: `#6F78FF` i usage, statuspunkt och progressbar.
- Codex-loggan behåller sin riktiga källgradient och vita terminaltecken.
- Claude använder den riktiga pixelkaraktären; ingen stjärna, emoji eller
  generisk AI-symbol ersätter den.
- Completion-pulsens djupa Codex-färg `#3D48FF` får finnas kvar som ett
  uttryckligt overlayläge, men den ordinarie agentraden ska inte längre ha en
  annan Codex-accent än usage-vyn.

Färg är stöd, aldrig enda betydelsebäraren. Provider, quota och status skrivs
också i text.

### Typografi och minsta storlek

- Huvudprocent: `plex_num_146`, inklusive `%` i samma label när det ryms.
- Provider/rubrik: 28–32 px.
- Quotaetikett: minst 24 px.
- Reset: minst 21 px, helst 24 px efter fysisk kontroll.
- Aktivitet: minst 21 px.
- Inga 12 eller 14 px-texter används för information som användaren behöver
  läsa på huvud-, detalj- eller översiktssidorna.
- Pixel-/monokänsla används i etiketter och status; den stora procenten förblir
  IBM Plex Sans Bold för maximal avläsning.

### Former och avstånd

- 18–24 px säker yttermarginal.
- Inga usage-kort, panelbakgrunder, etikettpiller, skuggor eller gradientsken.
- Separation görs med luft och högst en tunn hårfin linje.
- Progressbarer är 16–18 px höga och använder solida färger.
- Ingen mörk platta ligger bakom procenten.

## Sidor

### 1. Claude huvudvy

Claude-sidan visar endast den prioriterade verkliga modellgränsen, normalt
`WEEKLY · FABLE`. Om ingen separat modellgräns rapporteras används
`WEEKLY · ALL MODELS`; en Fable-gräns får aldrig fabriceras från aktiv modell.

Vyn innehåller, i ordning:

- kompakt VibePulse-märke och texten `CLAUDE`;
- quotaetiketten;
- huvudprocent i 146 px;
- en 16–18 px progressbar;
- en reset-rad, exempelvis `RESET FRI 08:00`;
- en enda kompakt aktivitetsrad.

Aktiv modell och effort, exempelvis `OPUS 4.1 · ULTRA`, visas endast medan en
färsk Claude-agent faktiskt arbetar och endast om den ryms utan att minska
provider, quota eller procent. Annars utelämnas den. Den är sekundär metadata,
inte permanent headerinnehåll.

### 2. Codex huvudvy

Codex-sidan följer exakt samma baslinjer och storlekar som Claude-sidan. Den
visar `WEEKLY`, omräknad förbrukad procent, Codex-accent, reset och den kompakta
aktivitetsraden.

Aktiv modell och effort visas under samma villkor som på Claude-sidan. Långa
modellnamn kortas i ett reserverat fält och får aldrig flytta procenten.

### 3. Claude detaljvy

Detaljvyn använder två fullbreddsrader utan kortbakgrunder:

1. `WEEKLY · ALL MODELS`
2. `5-HOUR LIMIT`

Varje rad visar en 82–96 px procent, solid progressbar och en reset-rad på
minst 21 px. Den separata modellgränsen upprepas inte här eftersom den redan
är Claudes huvudvy.

`+N TODAY` visas endast om en giltig, persistent baslinje finns inom samma
resetcykel. Saknas historik lämnas ytan tom; `0 TODAY` fabriceras inte.

### 4. Claude + Codex-översikt

Översikten delar skärmen i två luftiga, horisontella providerfält:

- Claude: `WEEKLY · FABLE` eller verklig modellgräns, stor förbrukad procent,
  progressbar och reset.
- Codex: `WEEKLY`, stor förbrukad procent, progressbar och reset.

Varje provider använder sin riktiga ikon, textnamn och accent. Genererade
stjärn-, kub- eller spökikoner är förbjudna. `2 ACTIVE` visas inte; en liten
statuspunkt vid respektive provider kommunicerar färsk arbetsstatus bättre.

Översikten är för armlängds avstånd och snabb jämförelse. Huvudvyerna förblir
de enda vyerna som lovar läsbarhet från 1–2 meter.

### Befintliga prognos- och volymvyer

`VECKOTAKT` och volymvyn behålls funktionellt men ingår inte i den första
statiska visuella leveransen. De flyttas efter huvud-, detalj- och
översiktssidorna och får senare anpassas till samma typ- och färgregler. De får
inte blockera den nya usage-layouten.

## Aktivitet utan informationsbrus

Aktivitet ska främst synas som rörelse, inte som en lång statusmening.

- `WORKING`: providerfärgad punkt andas långsamt och progressbarens ändpunkt
  får en diskret 1–2 px puls. En kort kategori som `EDITING FILES`, `RUNNING
  TESTS` eller `BUILDING` visas när den finns.
- Flera jobb: `CLAUDE · 2` eller `CODEX · 2` kan visas i aktivitetsraden. Hela
  projektlistor visas inte på usage-sidan.
- `NEEDS YOU`: amber, statisk text och ett långsamt dubbelslag. Ingen snabb
  blinkning.
- `ERROR`: röd, statisk text. Ingen upprepad helskärmsflash.
- Stale agentstatus döljer arbetspulsen i stället för att låtsas att agenten
  fortfarande arbetar.

Normal usage-vy står still. Ingen sida, quota eller provider byts automatiskt.
Animationer får aldrig flytta huvudprocentens baslinje.

## Klarsignal

Completion-beaconens kö, ljud, kvittens och autoåtergång behålls, men den
visuella sekvensen förenklas:

1. En kort, mjuk providerfärgad helskärmswash på 350–500 ms.
2. Statisk klarsida med riktig providerikon, stor `DONE` och sanerat
   projektnamn.
3. Ett kort providerljud spelas en gång.
4. Tryck kvitterar aktuell händelse; efter 10 s återgår skärmen automatiskt
   och lämnar en diskret `DONE`-status i providerfältet.

Ingen wash upprepas i loop. `WAITING`, `NEEDS YOU` och `ERROR` använder egna
stabila lägen och blandas inte ihop med `DONE`.

## Navigation

- KEY3 fortsätter byta Torget-app. VibePulse får inte ta över den knappen för
  provider- eller sidbyte.
- Horisontell swipe byter sida inom VibePulse.
- Simulatorns befintliga vänster/högerstyrning byter föregående/nästa sida.
- Om framtida hårdvara exponerar separata vänster/högerknappar kopplas de till
  samma sidnavigering; nuvarande kort exponerar endast KEY3 i
  hårdvarukontraktet.
- Långtryck öppnar fortsatt launchern.
- Tryck på klarsidan kvitterar endast aktuell completion-händelse.

Rekommenderad sidordning är Claude huvudvy, Codex huvudvy, Claude detaljvy,
Claude+Codex-översikt, därefter befintliga prognos-/volymvyer.

## AMOLED- och ESP32-regler

- Normalläget använder endast statiska objekt och små deluppdateringar.
- Arbetsanimation körs högst 6–8 fps och invaliderar bara statuspunkt,
  barändpunkt eller ikon, inte hela skärmen.
- Glow och programvarugradienter används inte i usage-objekt.
- Allokerade LVGL-objekt återanvänds; inga objekt skapas i renderloopen.
- Huvudlayouten använder befintliga fontassets, främst `plex_num_146`, för att
  undvika nya stora fontallokeringar.
- Hela layouten förskjuts 1–2 px enligt ett långsamt, deterministiskt mönster
  ungefär var 60:e sekund. Förskjutningen får aldrig ge klippning eller påverka
  touchmål.
- Befintlig nattdimning behålls. Inga stora vita statiska element visas med
  maximal ljusstyrka dygnet runt.
- Helskärmswash är kort och händelsestyrd; den körs aldrig kontinuerligt.

## Tillgänglighet och touch

- Kritisk status skrivs i text och kodas inte endast med färg eller animation.
- Alla touchytor som införs är minst 44 × 44 px. Hela klarsidan är ett enda
  kvittensmål.
- Ingen blinkning över 3 Hz tillåts.
- Rörelse kan stängas av genom ett kompilerbart/realt konfigurationsval utan
  att information försvinner.
- Layouten ska testas i panelens nattljusstyrka, inte bara i simulatorns fulla
  kontrast.

## Datakontrakt och fel

ESP32 fortsätter presentera ett bakåtkompatibelt `/api/tokens`-kontrakt.
Omräkningen för Codex sker i presentatör/policy, inte genom att skriva över
källvärdet. Tester ska därför kunna verifiera både `remaining=43` och
`used=57`.

Varje quotaobjekt behöver procent, reset och en betrodd etikett. Historikfält
är valfria. Ett fel i historik, modell, effort eller agentstatus får aldrig
underkänna en giltig huvudprocent.

Vid nätfel står senast godkända quota kvar enligt befintlig stale-policy. En
diskret `STALE`-markering får visas på armlängdsnivå; skärmen får inte bli tom
eller börja blinka.

## Leveransordning

1. Presentatörstester för enhetlig `USED`-semantik och vedertagna engelska
   etiketter.
2. Statisk Claude- och Codex-huvudvy i exakt 480 × 480.
3. Simulatorbilder jämförs mot den godkända stora procenthierarkin.
4. **Fysisk AMOLED-titt direkt efter de statiska huvudvyerna.** Typstorlek,
   marginaler, barhöjd, färg och läsbarhet justeras innan nästa steg.
5. Statisk Claude-detaljvy och tvåprovidersöversikt med riktiga assets.
6. Ny fysisk AMOLED-titt av alla fyra statiska vyer.
7. Implementera små arbetsanimationer och AMOLED-pixelförskjutning.
8. Anpassa completion-wash och klarsida; verifiera ljud och kvittens.
9. Återanslut befintliga prognos-/volymvyer efter de prioriterade sidorna.
10. Kör hosttester, C-tester, simulator, targetbuild, flash, bootlogg och
    slutlig fysisk kontroll.

## Verifiering

- Presentatörstest: Codex `43% remaining` ger `57% USED`, med clamp för
  gränsvärden och saknade data.
- Labeltest: verklig Claude-modellgräns bevaras; Fable fabriceras aldrig.
- Layouttest: inga kritiska 12/14 px-labels, inga card-bakgrunder och 16–18 px
  progressbarer på de fyra prioriterade sidorna.
- Assettest: riktiga Claude- och Codex-assets används; inga fallbacksymboler
  förekommer i normalläge.
- Navigationstest: swipe/simulator vänster-höger byter sida, KEY3 byter app,
  långtryck öppnar launcher och completion-tryck kvitterar endast eventet.
- Animationstest: högst 8 fps i normalläge, ingen animation flyttar procenten
  och stale status stannar pulsen.
- Screenshotdump: exakt 480 × 480 för alla fyra prioriterade vyer, saknad
  quota, stale data, `WORKING`, `NEEDS YOU`, `ERROR` och `DONE`.
- Fysisk kontroll: huvudprocent/provider på 1–2 meter; reset/aktivitet på
  armlängd; dags- och nattljusstyrka; rotation och touch.
- Targetkontroll: internminne, största DMA-block, nätuppdatering och display-
  flush förblir stabila under aktivitet och completion-wash.

## Klart när

- Claude och Codex har varsin lugn huvudvy med en dominant 146 px-procent.
- Alla huvudbarer betyder förbrukad quota och rör sig åt samma håll.
- Standardiserade engelska quota-namn används utan påhittade labels.
- Inga automatiska usage-växlingar eller kortbakgrunder finns kvar.
- Detaljvyn rymmer `WEEKLY · ALL MODELS` och `5-HOUR LIMIT` utan kritisk
  småtext.
- Översikten visar båda providers med riktiga ikoner och utan generiska
  ersättningssymboler.
- Arbete, behov av användaren, fel och klart kan skiljas på håll utan att
  usage försvinner permanent.
- Simulator och två fysiska statiska AMOLED-kontroller är godkända före full
  animation och ljud.
