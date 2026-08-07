# VibePulse – statisk AMOLED-granskning

**Datum:** 2026-08-07

**Status:** Simulator godkänd, fysisk kontroll pågår
**Hård grind:** Animation och kortrotation är avstängda tills användaren har
godkänt fotografierna från panelen.

## Simulator

Den statiska implementationen renderades från samma `app.c`,
`usage_screen.c` och `agent_monitor.c` som targetbygget använder. Följande
480 × 480-framebuffers skapades deterministiskt:

- `/tmp/torget-vibepulse-claude-static.bmp`
- `/tmp/torget-vibepulse-claude-long-copy.bmp`
- `/tmp/torget-vibepulse-codex-static.bmp`
- `/tmp/torget-vibepulse-forecast-collecting.bmp`
- `/tmp/torget-vibepulse-forecast-outcomes.bmp`
- `/tmp/torget-vibepulse-forecast-unavailable.bmp`
- `/tmp/torget-vibepulse-volume.bmp`
- `/tmp/torget-vibepulse-claude-missing.bmp`
- `/tmp/torget-vibepulse-claude-restored.bmp`

Kommandot `./sim/build/torget-sim --vibepulse-static-qa` tvingar layout och
redraw före varje snapshot och avslutas därefter. Tre rena körningar gav
byte-identiska SHA-256-hashar för hela matrisen.

Claude-vyn jämfördes i samma bild som den godkända 480 × 480-mockupen.
Procenten är dominant, korten är neutrala, dagens quotaökning har en separat
ljus del, resettexten klipper inte och aktivitetsgruppen är centrerad.
Codex-vyn har ett enda större veckokort och verklig providerikon.
Saknad quota visar streck och `QUOTA SAKNAS`; utgången agentlease tömmer
aktivitet och projektnamn.

## Avsiktliga skillnader mot brainstorm-mockupen

- Procenten är större för läsbarhet på avstånd.
- Fyra globala sidprickar motsvarar Claude, Codex, VECKOTAKT och volym.
- Ingen blå kortmarkering används; färg reserveras för provider, quota och
  aktivitet.
- Reset visas som sann återstående tid. Exakt lokal veckodag/tid visas först
  när kontraktet kan leverera den utan att ESP32:n gissar.

## Fysisk kontroll

Fylls i efter targetbuild, flash och fotografi.

| Kontroll | Observation |
|---|---|
| Svartnivå | Väntar på panel |
| Ljusstyrka | Väntar på panel |
| 73/47 % på avstånd | Väntar på panel |
| Modell/effort | Väntar på panel |
| Kortmarginaler och bezel | Väntar på panel |
| `V.` och providerpets | Väntar på panel |
| Lång aktivitetscopy | Väntar på panel |

## Verifiering före flash

- C-hosttester: gröna.
- Simulatorbuild med LVGL 9.5: grön.
- Simulatorns 480 × 480-matris: visuellt granskad.
- Pythonserverns 114 tester: gröna.
- ESP-IDF 5.5.2-targetbuild: grön, 63 % av minsta apppartitionen kvar.
- `git diff --check`: grön.
