---
description: Hitta verkligt dödkött i repot och föreslå borttagning med bevis. Fas 1 rapporterar bara — radering kräver godkännande.
argument-hint: [scope, t.ex. docs/ eller tools/ — utelämna för hela repot]
---

# Repo cleanup — inventera först, radera sedan

Scope: **$ARGUMENTS** (tomt = hela repot).

Städning är två jobb med motsatt riskprofil: att **hitta** kandidater är
billigt och ofarligt, att **ta bort** dem är inte det. Gör dem i den
ordningen och blanda dem aldrig. Det här körningen producerar en rapport.
Du raderar ingenting förrän jag har godkänt specifika rader i den.

Läs `CLAUDE.md`, `AGENTS.md` och `README.md` innan du börjar — de säger vad
som är bärande i det här repot.

## Fas 1 — inventering (den här körningen)

Arbeta kategori för kategori. Varje kandidat får en rad med **bevis**, aldrig
ett intryck. "Ser oanvänd ut" är inte ett bevis.

### De sex kategorierna

1. **Regenerbara artefakter som ligger spårade** — byggutdata, cacher,
   lockfiler för verktyg ingen kör, vendrade kopior som ett hämtskript redan
   producerar. Bevis: kommandot som återskapar filen.
2. **Överspelade engångsdokument** — utredningsanteckningar, körscheman för en
   session, brainstorms vars slutsats sedan landat i ett permanent dokument
   eller i koden. Bevis: var slutsatsen bor nu.
3. **Föräldralösa assets** — bilder, fixtures, fonter, exempelpayloads som
   inget refererar. Bevis: sökningen som kommer tillbaka tom — sök på
   *innehåll*, inte bara filnamn: assets refereras via glob, via katalog och
   från genererade filer.
4. **Död kod** — symbol, fil eller modul utan anropare och utan byggpost.
   Bevis: både grep-resultatet och frånvaron i CMake/Kconfig/`__init__`/exports.
5. **Dubblering** — samma sak sagd i tre dokument, samma hjälpfunktion skriven
   två gånger. Bevis: båda platserna, och vilken du föreslår att behålla.
6. **Rutten konfiguration** — ignore-regler för sökvägar som inte finns,
   CI-steg för jobb som togs bort, beroenden inget importerar.

### Aldrig kandidater

Rör inte dessa, oavsett hur oanvända de ser ut:

- `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `docs/releases/`
- `secrets.h.example` — mallen ÄR dokumentationen av konfigurationsytan
- `spec/` — hårdvarusanningen; `docs/lessons.md` — varje post är ett ärr
- `third_party/`, `dependencies.lock`, `partitions.csv`, `sdkconfig.defaults`
- `platform/fonts/*.c` — genererade men committade med flit (bygget ska
  varken behöva node eller nät)
- Allt som `.gitignore` täcker **och som inte är spårat**. En fil slutar inte
  vara spårad för att en ignore-regel tillkommer efteråt — den ligger kvar i
  repot och är då en giltig kandidat i kategori 1. Fråga `git ls-files`, inte
  `git check-ignore`
- Git-historik. Ingen `filter-branch`, ingen BFG, ingen force-push.

### Fällor i just det här repot

- **Dokumenten är bärande.** `test/` innehåller tester som hävdar att
  specifika dokument existerar och innehåller specifika saker
  (`test_docs_frame_drift.py`, `test_ota_gesture_docs.py`,
  `test_interaction_relay_docs.py`, `test_shared_amoled_skill.py`). Ett
  "oanvänt" dokument under `docs/superpowers/reviews/` kan vara det enda
  beviset ett test letar efter, eller en `locator:` i
  `spec/hardware-sources.yaml`. Greppa alltid `spec/`, `test/` och
  `tools/**/test_*.py` innan du föreslår ett dokument — mönstret är
  `test_*.py`, inte `*_test*.py`, som matchar noll filer här och just därför
  hade tystat `tools/test_hardware_registry.py`, som kräver ett dokument
  under `docs/superpowers/plans/`.
- **C-kod kan nås utan anropare** — via registermakro, Kconfig-symbol,
  `CMakeLists.txt`-post eller ett svagt symbolöverlagrat default. Grep efter
  namnet räcker inte; bygget är facit.
- **Bilder i README och release-kroppar** refereras både relativt och som
  absoluta `raw.githubusercontent.com`-URL:er. Sök på filnamnet i hela repot,
  inte bara på relativa sökvägar.
- **Stora binärer** är sällan skräp bara för att de är stora. Föreslå
  komprimering eller nedskalning som eget alternativ till radering.

### Rapportens format

En rad per kandidat, grupperad per kategori, sorterad på trygghet (tryggast
först):

| Sökväg | Storlek | Senast rörd | Refereras av | Varför säker att ta bort | Trygghet |

Plus två saker som gör rapporten trovärdig:

- **Behåll-listan**: det du övervägde och förkastade, med en rad om varför.
  Utan den vet jag inte om du tittade eller gissade.
- **Osäkerhetslistan**: det du inte kunde avgöra, med den fråga som skulle
  avgöra det. Gissa inte i tabellen ovan — flytta hit i stället.

Avsluta med totalsumman: antal filer och megabyte per kategori. Radera inget.
Committa inget. Fråga vad jag vill plocka.

## Fas 2 — utförande (först efter godkännande)

När jag har pekat ut vilka rader som gäller:

1. **Rent arbetsträd först.** `git status --porcelain` innan första
   raderingen: är någon godkänd sökväg ändrad eller ospårad, stanna och
   fråga. En radering plus commit kastar redigeringar gjorda efter
   inventeringen, och att reverta committen ger tillbaka den *committade*
   versionen — inte det som gick förlorat.
2. **Grön baslinje först.** Kör grinden EN gång innan första raderingen och
   skriv ner vad som är rött. Är den redan röd, rapportera det och stanna:
   utan baslinje tillskrivs ett existerande fel den första raderingen, och
   giltigt godkänt arbete revertas i onödan.
3. **En commit per kategori**, aldrig en stor. En dålig bedömning ska kunna
   revertas ensam.
4. **Grinden mellan varje commit**: `./test/run.sh` (hela värdgrinden, inte en
   delmängd), plus `idf.py build` så fort något target-byggets indata rörts —
   C, CMake, `Kconfig*`, `idf_component.yml`, `sdkconfig.defaults`,
   `partitions.csv`. Värdgrinden konfigurerar inte målet, så en trasig
   Kconfig-rad passerar den obemärkt. Röd grind = återställ den committen och
   rapportera, inte "fixa testet".
5. **Minsta möjliga ändring.** Städa inte formatering, döp inte om, flytta
   inte saker "medan du ändå är där". Ta bort det som godkändes.
6. **Följdändringar hör till samma commit** — dör ett dokument ska länkarna
   till det dö samtidigt. Lämna aldrig en trasig länk efter dig.
7. **Ignorera inte i stället för att ta bort.** En ny `.gitignore`-rad tar inte
   bort filen ur historiken och löser ingenting; ta bort filen, eller låt bli.
8. **Pusha bara det du skapade.** Notera `git rev-parse @{u}` före arbetet
   och jämför efteråt: ligger grenen före sin upstream med commits som inte
   är dina städcommits, publicerar en naken `git push` dem också — och
   godkännandet gällde rader i en rapport, inte någon annans halvfärdiga
   arbete. Stanna och fråga i så fall. Ingen PR om jag inte ber om det.
