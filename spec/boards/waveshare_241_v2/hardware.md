# Waveshare 2.41 V2 hardware profile

This directory is a separate registry for `waveshare_241_v2`. The root `spec/`
registry still describes the 2.16 board. Never transfer physical verification
between them. Validate with:

```sh
python tools/hardware_registry.py spec/boards/waveshare_241_v2
```

Read `hardware-capabilities.yaml`, `hardware-sources.yaml`, `device-units.yaml`
and `hardware-opportunities.md` here alongside this file. The [setup guide](../../../docs/waveshare-241-v2.md)
and [physical report](../../../docs/superpowers/reviews/2026-09-17-waveshare-241-v2-physical.md)
distinguish vendor facts, implemented support and tests on the named unit.

| Resource | V2 mapping |
|---|---|
| Compute/storage | ESP32-S3, 16 MB flash, 8 MB octal PSRAM; identified by flash tool and boot log |
| Panel/controller | RM690B0, native portrait 450 × 600; VibePulse landscape 600 × 450 |
| QSPI SPI2 | CS9, CLK10, DATA0–3 on GPIO11/12/13/14; 40 MHz, RGB565 |
| I2C0 | SDA47, SCL48 |
| Reset expander | TCA9554 at 0x20; LCD EXIO0, touch EXIO1; high/low/high reset pulses |
| Touch | FT6336, vendor FT5x06 driver, interrupt GPIO3 |
| Landscape pair | MADCTL 0x30, panel gap 0/16; touch x_max449/y_max599, mirror Y then swap XY |
| Portrait gap | 16/0; present in factory/Arduino code, missing in one LVGL example |
| Settings input | BOOT GPIO0 active-low; also a boot strap |
| GPIO18 | Expander interrupt; never initialize it as 2.16 KEY3 |
| LCD TE | GPIO21, not enabled for synchronization in this port |

The vendor instantiates `esp_lcd_sh8601` with its RM690B0 initialization table.
The software component's name must not replace the controller identity. The
SH8601 transport does not implement hardware `swap_xy`; select the vendor's
MADCTL in the initialization sequence and keep the touch transform paired.

The shared UI uses two-pixel flush alignment. Eight rows × 600 pixels × two
bytes = 9,600 bytes per transfer, below the original 11,520-byte transfer
budget. No extra persistent canvas/framebuffer was introduced. Source-specific
geometry retains the public 480 × 480 app contract; physical size is a separate
board property. Fonts and bitmaps are not scaled at runtime.

V1 wiring is incompatible and has not been ported. IMU rotation, audio, battery,
RTC, microSD, BLE and other peripherals are not validated by a successful
display/Wi-Fi boot. Existing 2.16 physical evidence does not cover this board.
