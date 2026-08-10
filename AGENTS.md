# Torget — appplattform för hyllskärmen

(Arbetsnamn — Niclas kan döpa om.) Torget äger den fysiska skärmen
(Waveshare ESP32-S3-Touch-AMOLED-2.16, 480×480) och producerar DEN enda
firmware-binär som flashas. Appar pluggar in som ESP-IDF-komponenter och kan
bo i egna repon. En skärm = en binär = ett bygge här. MIT-licens.

Struktur, byggkommandon och hur man skriver en app: **README.md** (läs den
först). Hårdvarusanningen: **spec/hardware.md**. Designsystemet:
**spec/ui-spec.md**. Historiken och P-numren: Solceller-repots
`docs/roadmap-hyllskarmen.md` — P25 + P25-VISION är detta repos
födelseattest.

## Status (2026-08-06, utbrytningen klar)

Plattformen kopierades (flyttades inte) från `~/Documents/Solceller/firmware/`
och stöptes om enligt granskningens tre krav: (1) versionerat appkontrakt
`torget_app_t` + appregister i main/registry.c som launchern läser;
(2) nätverk/hämtning bor i varje apps egen komponent (net.c), plattformen
äger bara WiFi/SNTP/lås/ljus/rotation; (3) MIT-licens. Två appar:
Solelkollen (datakontraktet mot solelkollen.se oförändrat) och Tokenmätaren
(Claude Code-användning via tools/tokenserver på Macen, platt JSON över LAN).

**Solceller-kopian fortsätter driva skärmen tills detta repo bevisat sig med
en lyckad flash + fysisk verifiering.** Verifierat hittills: hosttesterna
gröna, simulatorn BMP-dumpar alla vyer + launchern + Tokenmätaren korrekt,
targetbygget går igenom. Inte verifierat: flash på glaset.

## Arbetsregler

- **Bänk/sim först, flash sen.** Simulatorn är specen; en obevakad körning
  dumpar /tmp/torget-*.bmp som pixelfacit.
- **Värdlagren är tunna.** platform/, components/app_* (utom net.c) och
  kärnkomponenterna delas byte-identiskt mellan sim och target. UI-beteende
  hör hemma i appen/platform/, aldrig i main/main.c eller sim/main.c.
- **Kontraktet är heligt.** Bryts torget_app.h eller torget.h bumpas
  TORGET_APP_API_VERSION — launchern hoppar över appar med fel version.
- **Biblioteksregeln:** skörda vid ANDRA användningen, bygg aldrig i förväg
  (torget_fmt, torget_ticker, torget_net skördades när Tokenmätaren blev
  andra användaren — det är mallen).
- **Ärlighetsinvarianten:** aldrig påhittade nollor — utan data visas
  streck; räknare backar aldrig; eSett är AVRÄKNAD el och copyn säger det.

## Hårdvarufällorna i kortform (detaljer i spec/hardware.md)

- `bsp_display_lock()` LJUGER (esp_err_t genom bool, spegelvänt) — använd
  `esp_lv_adapter_lock(-1)` direkt (plattformens torget_ui_lock gör rätt),
  annars heapkorruption och eviga loopar.
- LVGL 9.5: `lv_span_set_text` ritar INTE om — explicit `lv_spangroup_refresh`.
- IMU QMI8658 svarar på **0x6B**; komponentens header deklarerar funktioner
  som inte finns i källan (`read_accel_mg`) — använd `read_accel`.
- Rotationskalibrering: SG_QUAD_UP 1, SG_QUAD_DIR -1, trösklar med
  iterationshistorik i rotation.c. Kalibrera fysisk hårdvara med ETT
  strukturerat flerlägestest, aldrig fotoforensik; en konstant per iteration.
- S3:an är 2,4 GHz-only; bootskanningen i loggen är facit för vilka nät som
  finns. MADCTL och touch roteras ALLTID i samma grepp.
- ESP-IDF 5.5.2 i `~/esp/esp-idf` (`. ~/esp/esp-idf/export.sh`), LVGL pinnad
  9.5.0 i BÅDE sim och target (bump båda i samma commit).

## Medvetet SENARE (bygg inte förrän triggern slår)

- WiFi-provisionering + OTA + konfig-UI — trigger: första enheten som lämnar
  huset (kompilerad secrets.h duger inte för sålda enheter; ESP-IDF:s
  wifi_provisioning finns färdig).
- Responsiv layout för andra Waveshare-storlekar — trigger: andra skärmtypen.
- Butiks-/paketmaskineri och appar i egna repon via Espressifs registry —
  trigger: bevisad traktion efter open source.
- **AI-buddyn** (eget repo) — mikrofoner/högtalare finns på kortet
  (ES7210/ES8311, oanvända). Röst kräver OpenAI Realtime API med egen
  betalning (ChatGPT-prenumeration ger ingen API-åtkomst). Arkitektur:
  kort ↔ websocket ↔ liten server.

## Hardware-aware work

Before proposing external hardware, declaring a device limitation, or designing
a hardware-dependent feature, read `spec/hardware.md`,
`spec/hardware-capabilities.yaml`, `spec/hardware-sources.yaml`, and
`spec/hardware-opportunities.md`. State whether the idea is only
silicon-capable, board-wired, firmware-enabled, and physically verified on the
named unit. Mention a relevant unused onboard capability when it materially
improves the request. Never copy secrets or turn an opportunity into authorized
implementation work.
