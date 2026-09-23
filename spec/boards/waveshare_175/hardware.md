# Waveshare 1.75 round preview profile

The owner read **1.75** on the unit. PCB revision remains unknown. This is
not the 1.75C profile. Do not select drivers from the 2.16 or 1.91 boards.

Read the four sibling registry files with this document. Vendor documentation
specifies 466 × 466 CO5300 QSPI AMOLED, CST9217 touch, ESP32-S3R8, 16 MB flash
and 8 MB PSRAM. These are vendor facts, not verification of this unit.

The current `waveshare_175` selector enables native LVGL quota previews only.
The firmware CMake entry point deliberately refuses this profile while its
BSP and all global surfaces remain unfinished. No firmware has been built or
installed and no physical capability has passed in this task.

## Board mapping to verify before a diagnostic build

The vendor hardware reference specifies QSPI CS12, CLK38, DATA0–3 on GPIO4–7,
LCD reset39, touch reset40/IRQ11, and I2C SDA15/SCL14. Touch address is 0x5A,
AXP2101 is 0x34, TCA9554 is 0x20, and BOOT is GPIO0. Verify against the exact
schematic and maintained BSP revision before enabling pins or power rails.
LCD and touch reset are separate GPIOs. Existing IMU rotation calibration and
other boards' expander-reset sequences are not portable evidence.

The prospective partial transfer is 466 × 8 × 2 = 7,456 bytes. This is an
arithmetic budget, not a measured largest-DMA-block result or a stress pass.
The preview adds LVGL arcs/labels, no canvas or persistent full framebuffer.

The existing 480px app contract translates by -7 on each axis without scaling.
Only the round quota layouts, native Wi-Fi indicator placement and diagnostic
are reviewed here; settings, provisioning, attention, analytics, launch and
rotation must pass their own circular fit review before support is announced.
