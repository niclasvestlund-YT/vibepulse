# UI-spec för hyllskärmen (LVGL-portens facit)

Extraherad ur webbänken `/device` (app/components/device/DeviceScreen.tsx +
app/globals.css) 2026-07-17. Bänken är specen: ändra aldrig här utan att ändra
webben först. Alla mått i panelpixlar på 480 × 480.

## Grund

- Botten: `#000000`, äkta svart (släckta pixlar på AMOLED).
- Skärmyta: 480 × 480. Innehållsyta per vy: padding 26 topp, 24 sidor, 42 botten
  (bottenutrymmet rymmer prickindikatorn).
- Font: IBM Plex Sans. Siffror ALLTID tabulara.
- Accent på enheten: `#ff9f2f` (mörklägesambern, framtagen för svart botten).
  Webbänken råkar ärva sidans tema här; enheten pinnar mörklägesvärdet.
- Text: vit `#ffffff` för hjältesiffror och värden, `#8994a5` för etiketter
  och enheter, `#6b7788` fasta decimaler, `#3f4855` strömmande decimal,
  `#5c687b` payback-etikett, hårlinje `#232d3b`, spår `#1d2634`,
  inaktiv prick `#2b3442`.
- Elprisnivåns prick: nivå -1 `#6fc795` (billigt), 0 `#8994a5` (normalt),
  1 `#e08a63` (dyrt). 11 × 11 px, rund, baslinjecentrerad efter enheten.

## De tre vyerna (lv_tileview, horisontell)

### Vy 1: Gett idag
- Eyebrow "GETT IDAG": 21 px, vikt 600, spärrning 0.17 em, versaler, `#8994a5`.
- Hjälterad (vertikalt centrerad i flexutrymmet mellan eyebrow och statraden):
  - Heltal: 146 px, vikt 700, spärrning -0.04 em, vitt. Innehåll: kronor med
    tusentalsavgränsare U+00A0.
  - Fasta decimaler ",dd": 50 px (0.34 em av 146), `#6b7788`, direkt efter.
  - Strömmande decimal (1 siffra): 50 px, `#3f4855`. Uppdateras ~10 Hz.
  - Enhet "kr": 32 px, vikt 600, `#8994a5`, 11 px vänstermarginal, baslinjelagd.
  - Utan data: ett streck "–" i heltalsstil, inget annat.
- Statrad (avgränsad med 1 px hårlinje `#232d3b`, 18 px padding-top,
  tvåkolumnsgrid med 16 px gap):
  - Etikett: 16 px, vikt 600, spärrning 0.14 em, versaler, `#8994a5`.
  - Värde: 38 px, vikt 700, spärrning -0.02 em, vitt, 4 px under etiketten.
  - Enhet (small): 17 px, vikt 500, `#8994a5`.
  - Vänster: "EFFEKT" + heltal W. Höger: "ELPRIS" + heltal öre + nivåprick.

### Vy 2: Gett i år
- Eyebrow "GETT I ÅR".
- Hjälterad: heltal kronor, 118 px (fyra siffror + tusentalsmellanrum är bredare
  än dagens tre; egen storlek, INTE nedskalning av samma etikett), enhet "kr" 32 px.
- Statrad, enkolumn: "SEDAN START" + `totalKr` grupperat + "kr".

### Vy 3: Återbetalt
- Eyebrow "ÅTERBETALT".
- Hjälterad: `paybackPct` med EN decimal, kommadecimal ("60,7"), 146 px,
  enhet "%" 32 px.
- Payback-sektion (18 px övermarginal):
  - Spår: höjd 5 px, radie 999, `#1d2634`. Fyllnad: accent, bredd = pct
    (klampa 100), mjuk breddanimation ~900 ms vid ändring.
  - Etikettrad under (7 px): "`totalKr` kr av `installationKr` kr", 15 px,
    vikt 600, `#5c687b`.

## Prickindikator

Tre prickar, centrerade horisontellt, 15 px från botten, 6 × 6 px, gap 7 px.
Inaktiv `#2b3442`, aktiv accent. Mjuk färgövergång ~220 ms.

## Beteenden

- **Svep**: tröskel 60 px horisontellt. Snäpp till närmsta vy, 320 ms,
  ease-out (webben: cubic-bezier(0.22, 0.61, 0.36, 1); LVGL: ease_out räcker).
  Vertikalt svep ignoreras.
- **Ticker**: vid varje lyckad hämtning: snappa till serverns `kr` (auktoritativ,
  även om det är bakåt). Däremellan: värde = bas + `krPerHour` × timmar sedan
  hämtning, klockat med `esp_timer_get_time()`. Etikettuppdatering 10 Hz.
  Endast vy 1 tickar; år/livstid/payback är statiska mellan hämtningar.
- **Hämtning**: GET `/api/glance` var 30:e sekund. Timeout ~8 s.
- **Stale**: > 120 s utan lyckad hämtning: behåll senaste värden, visa diskret
  stale-markering (förslag: eyebrown tonas till `#5c687b`; besluta i P7-anda,
  ALDRIG en ny sifferrad). Före första lyckade hämtningen: streck.
- **Pixeldrift** (inbränning): hela innehållsytan flyttas i cykeln
  (0,0) → (2,1) → (3,-1) → (1,-2), byte var 60:e sekund, animerat ~1200 ms.
  Prickindikatorn följer med.
- **Nattläge** (P7-beslut, förslag): när `w` = 0 och `kr` oförändrad > 30 min:
  panelljus ner till ~20 %. Väck till 100 % när `w` > 0 eller vid touch.

## Formatering (sv-SE, exakt)

- Tusental: U+00A0 (hårt mellanslag). 9524 → "9 524". 103147 → "103 147".
- Decimal: komma.
- Ticker-split (spegel av `splitTicking(v, 1)` i useLiveTicker.ts):
  skala = 1000; `n = llround(|v| × 1000)`; heltal = n / 1000 (grupperat);
  frac = två siffror nollutfyllt av (n % 1000) / 10; tail = (n % 10).
  Heltalsskalat så tickern ALDRIG visar ett öre för lågt.
- Payback: en decimal, komma. Aldrig procenttecken i strängen (enheten är egen etikett).
- API-värden är aldrig negativa; klampa mot 0 och logga om det ändå sker.

## Fonter (P3-facit)

| Namn | Storlek | Vikt | Glyfer | Används till |
|---|---|---|---|---|
| plex_num_146 | 146 | 700 | `0-9`, `,`, `–`, U+0020, U+00A0 | hjältetal vy 1+3 |
| plex_num_118 | 118 | 700 | `0-9`, U+0020, U+00A0, `–` | hjältetal vy 2 |
| plex_num_50 | 50 | 700 | `0-9`, `,` | decimaler vy 1 |
| plex_num_38 | 38 | 700 | `0-9`, `,`, U+0020, U+00A0, `–` | statvärden |
| plex_text_32 | 32 | 600 | `kr%` (k, r, %) | hjälteenheter |
| plex_text_21 | 21 | 600 | `A-Z`, `ÅÄÖ`, U+0020 | eyebrows |
| plex_text_16 | 16 | 600 | `A-Z`, `ÅÄÖ`, U+0020 | statetiketter |
| plex_text_17 | 17 | 500 | `a-zåäö`, `A-Z`, `0-9`, U+0020, U+00A0, `%` | small-enheter, paybackrad (15 px avrundas hit) |

Spärrning görs i LVGL via `lv_style_set_text_letter_space` (px, avrunda från em).
`lv_font_conv`-kommandon: se platform/fonts/fetch-and-convert.sh. OFL-licensen tillåter inbäddning.

## Vad som INTE finns på enheten

Ingen klocka, inget datum, ingen väderprognos, inga fler siffror. Liveness är
rörelse i befintliga element. En ny informationsrad på skärmen är ett regelbrott.
