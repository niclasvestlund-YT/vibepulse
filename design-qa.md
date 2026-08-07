# VibePulse design QA

**Resultat:** PASS i simulatorn. Fysisk AMOLED-grind återstår.

## Jämförelse

- Referens: den godkända 480 × 480-mockupen `Usage-vyn med verklig rörelse`.
- Implementation: faktisk 480 × 480-XRGB8888-framebuffer från LVGL-simulatorn.
- Reproducerbar körning: `./sim/build/torget-sim --vibepulse-static-qa`.
- Lokal sida-vid-sida-bild: `work/design-qa/comparison-claude.png`.
- Kontrollerade tillstånd: Claude arbetar, Claude lång väntetext, Codex
  arbetar, VECKOTAKT-skal, volym, saknad Claude-quota och återställd quota.

## Synlig granskning

- P0: inga blockerande visuella fel.
- P1: ingen klippning, överlappning eller felaktig hierarki vid 480 × 480.
- P2: procenten är avsiktligt större än i webbmockupen för fysisk
  avståndsläsning. Fyra prickar visar appens fyra globala sidor. Den blå
  markeringsramen från brainstorm-mockupen används inte eftersom den låsta
  specifikationen kräver neutrala kort.
- `V.`-identiteten, provider, modell/effort, båda Claude-korten och den
  centrerade aktivitetsgruppen ligger kvar i samma koordinater när status
  ändras; ingen helskärms-overlay finns längre.
- Långtexten `BEHÖVER GODKÄNNAN` ryms utan att flytta korten.
- Codex visar ett enda större veckokort och en tydlig blå provideraccent.
- Saknad quota visar `–` och den uttryckliga texten `QUOTA SAKNAS`.
- En utgången tvåminuterslease tömmer både aktivitet och projektnamn; gammal
  agentstatus får inte se aktuell ut i en dämpad färg.
- QA-läget tvingar layout och redraw före varje snapshot. Tre separata
  körningar gav byte-identiska SHA-256-hashar för hela bildmatrisen.

## Öppen hårdvarugrind

Typstorlek, svartnivå, fysisk ljusstyrka, bezelbalans och läsbarhet på
avstånd måste fortfarande bedömas på den riktiga AMOLED-panelen. Ingen
kortrotation, pet-rörelse eller pulsanimering får aktiveras före den grinden.
