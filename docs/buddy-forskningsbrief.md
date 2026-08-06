# AI-buddyn — forskningsbrief (startpunkt för egen session)

**Skrivet:** 2026-08-06, ur Torget-utbrytningssessionen.
**Läge:** ren research/spåning — Torget och agentmonitorn byggs klart först.
**Tänkt hem:** eget repo; buddyn blir en Torget-app (ESP-IDF-komponent som
exporterar en `torget_app_t` och pluggar in i main/registry.c).

## Vad som redan är bestämt (ur AGENTS.md / roadmapen)

- Arkitektur: **kort ↔ websocket ↔ liten server** (servern på Macen/molnet
  äger API-nycklar och tunga modeller; kortet är mik/högtalare/skärm).
- ChatGPT-prenumerationen ger INGEN API-åtkomst — röst i realtid kräver
  OpenAI:s Realtime API med egen betalning, eller en egen
  STT → LLM → TTS-kedja.
- Triggern för att börja bygga: Torget stabilt + agentmonitorn levererad.

## Hårdvarufakta att ärva (spec/hardware.md i Torget)

- Kodek **ES8311** (ut) + **ES7210** (mik-ADC, flera mikar) på delade
  I2C-bussen; PA-enable GPIO46; I2S: MCLK=42, BCLK=9, LRCK=45, DSDIN=8,
  ASDOUT=10. `esp_codec_dev` finns redan i komponentgrafen.
- Internminnet är den knappa resursen (~87 KB fritt i drift, största
  DMA-block ~31 KB) — ljudbuffertar ska vara små och engångsallokerade;
  PSRAM (8 MB) finns för större ringbuffertar.
- WiFi är 2,4 GHz — räkna latensbudget därefter.
- Plattformskontraktet: nätverk bor i APPEN; `torget_net_wait()`,
  `torget_ui_lock()`, `torget_keep_awake()` är värds-API:t.

## Forskningsfrågor för nya sessionen

1. **OpenAI:s officiella `openai-realtime-embedded-sdk`** (byggd för just
   ESP32-S3): status, licens, hur den hanterar eko/duplex, och om dess
   ljudväg samsas med vår BSP/adapter — eller om bara protokolldelen ska
   återanvändas.
2. **Realtime API vs egen kedja** (Whisper/STT → Claude → TTS): latens,
   pris per samtalsminut, avbrytbarhet (barge-in), svenska.
   Claude saknar realtime-röst-API — om buddyn ska ha Claude-hjärna blir
   det kedjevägen; jämför ärligt.
3. **Eko/duplex på kortet**: ES7210 har AEC-referenskanal — räcker
   hårdvaru-AEC eller krävs push-to-talk (KEY3 finns redan som knapp)?
4. **Serverform**: samma mönster som tokenserver (liten Python på Macen)
   eller något långlivat i molnet? Vad händer när Macen sover?
5. **Buddy-UI:t som Torget-app**: vad visar skärmen under lyssna/tänka/
   tala? (Agentmonitorns tillståndsspråk — stora ord, pixelfigur — är en
   naturlig släkting; återanvänd designspråket, inte koden.)
6. **Sekretessgräns**: vad lämnar huset? (Torget-regeln: skärmen visar
   härledd status, aldrig råinnehåll — vad är buddy-motsvarigheten?)

## Arbetssätt att ta med

Bänk/sim först, BMP-dumpar som pixelfacit, hosttester för varje parser,
fysisk granskning tidigt (fotona hittade det bänken missade), en konstant
per iteration vid hårdvarukalibrering, och biblioteksregeln: skörda vid
andra användningen. Läs Torgets README + spec/hardware.md +
docs/agentmonitor-granskning.md innan någon kod skrivs.
