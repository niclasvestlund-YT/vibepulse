# Torget

Appplattformen för hyllskärmen (Waveshare ESP32-S3-Touch-AMOLED-2.16,
480×480). Torget äger den fysiska skärmen och producerar DEN enda
firmware-binär som flashas; appar pluggar in som ESP-IDF-komponenter och
kan bo i egna repon. En skärm = en binär = ett bygge här. MIT-licens.

Utbruten ur [Solelkollen](https://solelkollen.se)s firmware (P25 i
Solceller-repots `docs/roadmap-hyllskarmen.md`); Solceller-kopian fortsätter
driva skärmen tills det här repot bevisat sig med en lyckad flash.

## Arkitekturen i tre meningar

**Plattformen** (main/ + platform/) äger panelen, WiFi, SNTP, LVGL-låset,
ljusrampen/nattläget, auto-rotationen, pixeldriften och launchern.
**Apparna** (components/app_*) äger allt annat: sina vyer, sina endpoints,
sina hämttasker och sin kadens — nätverk bor i APPEN, aldrig i plattformens
main. **Kontraktet** mellan dem är `torget_app_t { namn, ikon, create,
enter, leave }` (platform/torget_app.h, versionerat) plus värds-API:t i
platform/torget.h — appregistret i main/registry.c är det launchern läser.

## Katalogstruktur

```
platform/                 kontraktet + delat plattforms-UI (launcher, drift) + fonterna
main/                     ESP-värdlagret: boot, WiFi, SNTP, ljusramp, rotation, appregistret
components/
  torget_app/             kontraktskomponenten (bara headrar) — det appar byggs mot
  torget_net/             glance-mönstrets HTTP-klient (bara targetet)
  torget_fmt/             sv-SE-formatering, hosttestad
  torget_ticker/          den lokala tickern, hosttestad
  app_solelkollen/        app 1: fyra vyer, /api/glance + /api/glance-sverige
  app_tokens/             app 2: VibePulse (agentstatus + Claude/Codex-usage)
sim/                      SDL-simulatorn: hela plattformen + apparna på Macen
sim-fixtures/             inspelade API-svar simulatorn och testerna delar
test/                     hosttester, körs med clang utan ESP-IDF: ./test/run.sh
tools/tokenserver/        Mac-tjänsten som serverar VibePulse-data över LAN
spec/                     hardware.md (alla hårdvarufällor) + ui-spec.md (designsystemet)
third_party/cjson/        vendrad cJSON 1.7.18 — samma parser på Macen som på kortet
```

`platform/`, `components/app_*` (utom net.c) och kärnkomponenterna delas
BYTE-IDENTISKT mellan sim/ och targetet. Det enda som skiljer världarna åt
är värdlagren (main/main.c respektive sim/main.c). Ändrar du UI-beteende hör
det hemma i appen eller platform/, aldrig i något värdlager.

## Skriva en app

1. Ny komponent under `components/`, med egen `idf_component.yml`
   (`lvgl/lvgl: "9.*"`).
2. Exportera en `const torget_app_t` med `.api_version =
   TORGET_APP_API_VERSION`, namn, ikon (glyf + platta + accentprick) och
   `create(root)` som bygger UI:t i root-lådan.
3. Datat: en egen PLATT publik JSON-endpoint (glance-mönstret: tal inte
   strängar, en takt så appen kan ticka lokalt), en egen parser med
   kontraktsregler och hosttester, en egen hämttask i `net.c` som väntar på
   `torget_net_wait()` och matar appen under `torget_ui_lock()`.
4. Registrera i `main/registry.c` och lista de delade filerna i
   `sim/CMakeLists.txt`. Långtryck i appens UI ska kalla
   `torget_launcher_open()`.
5. Designregler: äkta svart botten, IBM Plex, sv-SE-format, aldrig påhittade
   nollor — utan data visas streck. Bänk/sim först, flash sen.

## Bygga och flasha targetet

```
. ~/esp/esp-idf/export.sh
cp secrets.h.example secrets.h     # fyll i WiFi + VibePulse-tjänstens URL
idf.py set-target esp32s3          # engångs, skapar sdkconfig
idf.py build
idf.py -p /dev/cu.usbmodem101 flash monitor
```

Hittas inget `/dev/cu.usbmodem*`: håll BOOT (GPIO0) nere, tryck och släpp
RESET, släpp BOOT — då räknas kortet upp i nedladdningsläge. `idf.py monitor`
avslutas med Ctrl+].

## Simulatorn (bänken)

```
cmake -S sim -B sim/build -G Ninja && ninja -C sim/build
./sim/build/torget-sim
```

Tangent 1-4 väljer Solelkollen-fixtur, T matar om VibePulse-usage, S cyklar
agentstatus, N växlar app (KEY3-knappens bänkmotsvarighet), L öppnar launchern (långtryck med
musen fungerar också — det är enhetens gest). På enheten växlar KEY3
(GPIO18) app med ett tryck.
En obevakad körning BMP-dumpar alla vyer + launchern + VibePulse till
/tmp/torget-*.bmp — pixelverifieringens facit.

## Hosttesterna

```
./test/run.sh
```

Kompilerar kärnorna + båda parsrarna med clang under -Wall -Wextra -Werror
och kör dem mot de riktiga fixture-filerna plus fientliga indata.

## VibePulse-tjänsten

`tools/tokenserver/` — liten Python-stdlib-tjänst som skannar Claude
Code- och Codex-loggarna och serverar `/api/tokens` samt den separata
agentstatusen över LAN. VibePulse sparar högst en content-fri quotapunkt per
15 minuter i åtta dagar för `+N% IDAG` och VECKOTAKT. Historiken innehåller
endast tid, leverantör, fönster, procent och resetcykel. Se README:n där för
kontrakt, integritetsgräns och autostart via launchd.

## Hårdvarufällorna

Allt som bits står i `spec/hardware.md` och beslutsloggen nedan är ärvd
därifrån i kortform:

- `bsp_display_lock()` LJUGER (esp_err_t genom bool, spegelvänt) — tala med
  `esp_lv_adapter_lock(-1)` direkt. Plattformen exponerar `torget_ui_lock()`.
- LVGL 9.5: `lv_span_set_text` ritar INTE om — `lv_spangroup_refresh` krävs.
- IMU QMI8658 svarar på **0x6B**; headerns `read_accel_mg` finns inte i
  källan — använd `read_accel`. Kalibrering: SG_QUAD_UP 1, SG_QUAD_DIR -1.
- MADCTL och touch roteras ALLTID i samma grepp (rotation.c gör det rätt).
- S3:an är 2,4 GHz-only; bootskanningen i loggen är facit för vilka nät
  som finns.
- LVGL pinnad till samma version i sim och target (9.5.0) — bump båda i
  samma commit (sim/CMakeLists.txt).

## Medvetet SENARE (bygg inte förrän triggern slår)

- WiFi-provisionering + OTA — trigger: första enheten som lämnar huset.
- Responsiv layout för andra Waveshare-storlekar — trigger: andra skärmtypen.
- Appbutik/paketmaskineri — trigger: bevisad traktion efter open source.
- AI-buddyn med röst (kodeken finns på kortet) — eget repo, kort ↔
  websocket ↔ liten server; Realtime-API med egen betalning.
