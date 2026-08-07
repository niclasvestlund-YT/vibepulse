# VibePulse statisk klarsignal — simulatorgranskning

**Datum:** 2026-08-07
**Status:** simulator och targetbuild gröna; inväntar fysisk AMOLED-granskning

## Granskade bilder

- `/tmp/torget-vibepulse-multi-working.bmp`
- `/tmp/torget-vibepulse-claude-done-static.bmp`
- `/tmp/torget-vibepulse-codex-done-static.bmp`
- `/tmp/torget-vibepulse-two-done-queued.bmp`

Bilderna är deterministiska 480 × 480-dumpar från samma LVGL 9.5-kod och
samma provider-assets som targetbygget använder.

## Resultat

- Usage-procenten är fortsatt huvudinformation medan jobb arbetar.
- Claude och Codex kan visas samtidigt i en gemensam 444 × 78-rad.
- Raden använder riktiga 32 px-assets och visar antal aktiva jobb, aktivitet
  och projekt utan rå prompt-, fil- eller kommandotext.
- Claude-klarsidan använder den riktiga pixelmasken i `#D97757`.
- Codex-klarsidan bevarar originalmolnets gradient och vita terminalglyfer.
- Båda klarsidorna använder hela panelen men lämnar tydlig luft mellan ikon,
  `KLAR` och projekt. Inga extra instruktioner eller dekorationer lades till.
- Ett tryck kvitterar bara aktuell klarsida. Långtryck öppnar launchern.
- Två samtidiga avslut hamnar i FIFO-kö; återstående jobb visas längst ned.

## Jämförelse mot referenser

Usage-referensen, Codex-originalet och simulatorbilderna granskades tillsammans
i samma visuella jämförelse. Providerformer och färger matchar källbilderna.
Den avsiktliga skillnaden mot usage-fotot är att den lilla statusraden kan
delas mellan två providers; quota-korten och deras stora siffror behåller
samma visuella dominans.

## AMOLED-grind

Animation och ljud är fortfarande avstängda. Nästa steg är att flasha denna
statiska version och kontrollera rakt framifrån att provider, `KLAR`, projekt
och dubbel jobbrad är läsbara på 2–3 meters avstånd. Puls och pip får inte
läggas till innan den kontrollen är godkänd.
