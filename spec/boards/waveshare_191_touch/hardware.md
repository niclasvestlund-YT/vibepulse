# Waveshare ESP32-S3-Touch-AMOLED-1.91

Profile: `waveshare_191_touch`. Touch version only. Owner confirmed the model;
PCB revision remains unknown. Vendor V1/V2 SD wiring differs; this port does
not initialize SD and does not infer revision from USB or screen size.

Read the four companion registry files. Source revision and wiring are in
`hardware-sources.yaml` and `hardware-capabilities.yaml`. No verification from
2.16 or 2.41 is inherited. Full flash backup was read and device-MD5 verified
before installation. Private backup is outside the repository.

Display: RM67162, transported through the vendor's SH8601 QSPI driver. Pin
mapping agrees across the factory, ESP-IDF landscape and Arduino examples.
Native touch reports portrait coordinates; the landscape pair mirrors X then
swaps axes. BOOT/GPIO0 controls settings; GPIO18 is a display data line.

The physical raster is 536x240. The unchanged 480px app root is centered at
x=28; compact surfaces use 240px height and native fonts, not runtime bitmap
scaling. Flush budget is 8,576 B, below the original 11,520 B. Static diagnostic
uses existing LVGL objects with no extra persistent canvas. OTA is disabled
for this new profile until board-specific image validation is implemented.
Owner photographs from September 23 show readable startup and Codex weekly
content on the physical panel. They do not verify calibrated color, the full
menu, motion or an approval round trip.
