# Waveshare 1.75 round preview profile

The owner read **1.75** on the unit. PCB revision remains unknown. This is
not the 1.75C profile. Do not select drivers from the 2.16 or 1.91 boards.

Read the four sibling registry files with this document. Vendor documentation
specifies 466 × 466 CO5300 QSPI AMOLED, CST9217 touch, ESP32-S3R8, 16 MB flash
and 8 MB PSRAM. These are vendor facts, not verification of this unit.

The `waveshare_175` selector enables the native VibePulse app and a separate
`TORGET_ROUND_DIAGNOSTIC=ON` recovery build. The normal app boots, joins Wi-Fi
and fetches tokens on the named unit. Its physical layout, touch alignment
and value accuracy still require owner review. The connected unit was first
found running a 1.91 recovery image; see the preview guide for the recovery
and device-selection lesson.

## Board mapping checked against the pinned vendor source

The vendor hardware reference specifies QSPI CS12, CLK38, DATA0–3 on GPIO4–7,
LCD reset39, touch reset40/IRQ11, and I2C SDA15/SCL14. Touch address is 0x5A,
AXP2101 is 0x34, TCA9554 is 0x20, and BOOT is GPIO0. These were checked against the schematic and maintained BSP at vendor
revision e4344e70; the unit PCB revision remains unknown.
LCD and touch reset are separate GPIOs. Existing IMU rotation calibration and
other boards' expander-reset sequences are not portable evidence.

The partial transfer is 466 × 8 × 2 = 7,456 bytes. The normal app measured
more than 51 KB as the largest DMA block after Wi-Fi portal startup; sustained
TLS and rotation stress remain unmeasured.
The preview adds LVGL arcs/labels, no canvas or persistent full framebuffer.

The existing 480px app contract translates by -7 on each axis without scaling.
The native renderer checks all app surfaces against the circle. A USB-down
quarter-turn is installed on the named unit; visual and touch confirmation
are pending. Automatic rotation has not been calibrated on this board.
