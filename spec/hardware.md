# Hårdvarufakta: ESP32-S3-Touch-AMOLED-2.16 (P2-recon, 2026-07-17)

Insamlat från docs.waveshare.com och Waveshares BSP-källkod på GitHub.
Viktigast: gamla wikin (www.waveshare.com/wiki/...) är en tom platshållare —
den riktiga dokumentationen är **docs.waveshare.com**, och demokoden ligger på
GitHub, inte i en zip. Varje sektion är märkt verifierad eller ej.

## How to read hardware truth

This file explains verified traps and context. Machine-readable state lives in
`hardware-capabilities.yaml`, source metadata in `hardware-sources.yaml`, and
per-device physical evidence in `device-units.yaml`. A feature of ESP32-S3 is
not automatically wired on this board, enabled in Torget, or verified on the
physical unit. Run `python3 tools/hardware_registry.py spec` after editing any
registry file.

## Beslut som reconen låser (verifierat)

| Fråga | Svar |
|---|---|
| ESP-IDF | **>= 5.5 krävs** (BSP-manifestet; exemplen visar 5.5.2). INTE 5.3. |
| LVGL | **9** (demon pinnar `lvgl/lvgl: "9.*"`) |
| Drivrutiner | ESP Component Registry: BSP **`waveshare/esp32_s3_touch_amoled_2_16`** v2.0.1, som drar in `espressif/esp_lcd_co5300`, `waveshare/esp_lcd_touch_cst9217`, `espressif/esp_lvgl_adapter ~0.6` |
| Touchkrets | **CST9217** (adress 0x5A) — produktbladen säger CST9220, dokumentationen och BSP:n säger CST9217; utgå från CST9217 |
| Vår main blir | i stil med demons: `bsp_display_start(); bsp_display_lock();` + vårt UI. Demons main är ~20 rader. |

## Pinallokering (verifierad mot docs + BSP-header)

**Display, CO5300 över QSPI (SPI2_HOST; `display.amoled`):** CS=12, PCLK=38,
SIO0-3=4/5/6/7, RESET=39. Ingen backlight-GPIO (AMOLED: ljus via
panelkommando).

**Touch CST9217 (I2C):** SCL=14, SDA=15, INT=11, RESET=40.

**Delad I2C-buss (port 1, 400 kHz, SCL=14/SDA=15):** touch CST9217 (0x5A),
PMU AXP2101 (0x34; `power.axp2101`), RTC **PCF85063ATL** (0x51;
`rtc.pcf85063atl`), IMU QMI8658 (0x6A/0x6B), kodek ES8311/ES7210
(0x18/0x40, komponentdefaults).

**Övrigt:** BOOT=GPIO0, KEY3=GPIO18 (aktiv låg), RTC_INT=13, IMU_INT=17/21,
SYS_OUT=16, TF-kort SDMMC 1-bit (CMD=1, CLK=2, D0=3), USB=19/20, UART0=43/44,
audio MCLK=42/BCLK=9/LRCK=45/DSDIN=8/ASDOUT=10/PA=46.

## BSP-bugg: bsp_display_lock ljuger (verifierad på hårdvara 2026-08-06)

`bsp_display_lock()` i BSP v2.0.1 returnerar `esp_lv_adapter_lock(timeout)`
rakt igenom en `bool` — men adaptern returnerar `esp_err_t`, där ESP_OK = 0.
Lyckat lås blir alltså **false** och misslyckat blir **true**. Kod som gör
`if (bsp_display_lock(...))` kör sin LVGL-mutation exakt när låset INTE togs.
Konsekvensen hos oss: UI-bygget rusade parallellt med adapterns lvgl-task,
LVGL-heapen (LV_OS_NONE, olåst TLSF) korrumperades och båda taskarna fastnade
i eviga loopar (`block_insert` respektive `find_track_end`) — watchdog på
IDLE0 var enda symtomet. Waveshares demo överlever av en slump: den låser
blockerande och läser aldrig returvärdet.

Regel: tala med `esp_lv_adapter_lock(-1)`/`esp_lv_adapter_unlock()` direkt
och kontrollera mot ESP_OK. Diagnosen ställdes med gdb över den inbyggda
USB-JTAG:en mot den levande hängningen — `openocd -f board/esp32s3-builtin.cfg`
plus `xtensa-esp32s3-elf-gdb build/solglance.elf`, och glöm inte att döda
openocd före nästa flashning (samma USB-enhet).

## WiFi-fakta (verifierade på hårdvara 2026-08-06)

- **2,4 GHz ENDAST.** Det Macen/telefonen ser är ofta 5 GHz-bandet; kortets
  bootskanning är facit för vilka nät som existerar i dess värld.
- Frånkopplingsorsak 201 = nätet syns inte (fel namn eller fel band),
  15/204 = fel lösenord. Orsaken loggas av vår event-handler.
- En 2,4 GHz-hemmesh på kanal 13 med WPA2 (auth 3) både skannades och
  anslöts 2026-08-06; kanal 13 fungerar i EU-regdomänen.

## CO5300-gotchas (verifierade i BSP-källkoden; `display.amoled`)

- **AXP2101 behöver INTE konfigureras** för att panelen ska lysa — BSP:n rör
  aldrig PMU:n. Default-rails räcker.
- **Ljusstyrka = panelkommando 0x51 (DBV)** via `bsp_display_brightness_set(0-100)`.
  Detta är nattdimningens mekanism (P7) — färdig funktion finns.
- **Initsekvensen tar ~1,2 s** (sleep-out 600 ms + display-on 600 ms):
  planera uppstartsupplevelsen, panelen är svart länge nog att det märks.
- **QSPI 40 MHz**, mode 0, cmd_bits=32. Över 40 MHz är oprövat: rör inte.
- **RGB565, big-endian byteordning** (`BSP_LCD_BIGENDIAN=1`), COLMOD 0x55.
- **MADCTL 0x36=0xA0 i init** (panelen sitter roterad) och touchen kompenserar
  med `swap_xy=1, mirror_y=1` — **behåll paret**, ändra aldrig bara ena sidan.
- **2-pixel-alignment krävs**: BSP:n registrerar en rounder-callback som rundar
  dirty-areor till jämn start/udda slut i x och y. Utan den: pixelskräp.

## LVGL-bufferstrategi (verifierad BSP-default)

**Partiell dubbelbuffert 480×50 px i PSRAM** (`buffer_height=50`,
`use_psram=true`, `require_double_buffer=true`), tear-avoidance av.
Alltså INTE helskärmsbuffert som tidigare gissat — 480×50×2 är vad Waveshare
skeppar och det är startpunkten. Helbild går via
`bsp_display_start_with_config()` om partiell visar sig hacka i svepet;
det är ett P13-experiment, inte ett förhandsbeslut.

sdkconfig.defaults i demon: flash QIO 16 MB, PSRAM oktal 80 MHz + XIP,
CPU 240 MHz, `LV_DEF_REFR_PERIOD=15`, 2 SW-draw-units, partition 8M app + 7M SPIFFS.

## Länkar

- Dokumentation: https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16
- ESP-IDF-guide: https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16/Development-Environment-Setup-ESP-IDF
- Exempelrepo: https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-2.16
  (examples/esp-idf/02_lvgl_demo_v9 är vår mall; fabriksfirmware för
  återställning ligger i repots firmware/-katalog)
- BSP-källkod: https://github.com/waveshareteam/Waveshare-ESP32-components
- BSP i registryt: https://components.espressif.com/components/waveshare/esp32_s3_touch_amoled_2_16
- Schema-PDF: https://files.waveshare.com/wiki/ESP32-S3-Touch-AMOLED-2.16/ESP32-S3-Touch-AMOLED-2.16-Schematic.pdf

## Kvarvarande luckor (ej verifierat)

Reconen 2026-07-17 lämnade två frågor öppna; aktuellt verifieringsläge och
källor finns i registret:

- Vilken AXP2101-rail som matar AMOLED:en: `power.axp2101`.
- Om 40 MHz är panelens tak eller bara komponentdefault: `display.amoled`.
