# Waveshare 3.97 e-paper USB board registry

This registry is separate from every AMOLED model. Select the dedicated
`firmware/epaper_397` ESP-IDF project; do not pass an invented `TORGET_BOARD`
value to the LVGL build. Native raster: 800×480, MSB-first monochrome.

Confirmed package: SKU 33811 kit. PCB revision unknown. Original app descriptor
matched `ESP32-S3_ePaper-3.97`; ROM identified 16MB flash/8MB PSRAM.
USB identity alone is not model identification.

Panel SPI3: SCK11, MOSI12, CS10, DC9, RST46, BUSY3. Three-way switch: 4/5/6.
I2C SDA41/SCL42. Panel power: TG28/AXP2101-compatible PMU, address 0x34,
chip ID 0x4a, ALDO3=3.3V. Preserve unrelated rails and charger registers.
The simple vendor example omits this power path; the factory example includes it.

Keep baseline RAM and panel power between partial refreshes. Startup/full cleanup
reset the baseline; USB heartbeats do not consume a refresh. Ten partial changes
is the initial cleanup budget, not a measured endurance guarantee.

Read the [setup guide](../../../docs/waveshare-397-epaper.md) and
[physical report](../../../docs/reviews/2026-10-28-epaper397.md). Firmware build,
write verification, protocol ACK and human pixel checks are separate evidence.
No battery, audio, Wi-Fi/OTA, sensors, storage or remote-decision support is claimed.
