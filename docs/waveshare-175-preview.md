# Round 1.75 design preview — not installable support

The owner chose to test a circular quota ring, USED TODAY and a D:H:M
countdown. The native LVGL preview uses the same quota presenter and the
same daily-usage validation as the existing screens. It is a design test,
not a completed board port, release or physical-review report.

```sh
PYTHON_BIN=.venv/bin/python tools/preview-ui.sh vibepulse waveshare_175
```

The returned private directory contains 466 × 466 PNGs: live and cached/stale
with identical values; missing total, today or reset; contradictory today;
zero and full usage; longest countdown; early exhaustion; wide quota copy; and five-target touch diagnostics.

## Design and semantics

- Native Plex fonts; fixed Claude #D97757 and Codex #6F78FF accents.
- One dominant percentage. The muted ring is earlier usage, the bright
  terminal segment is today's contribution to the same allowance.
- Missing today is a dash, not zero. Invalid today does not draw a misleading
  partition. Zero and 100% are tested separately.
- The countdown is DD:HH:MM, without seconds. The accompanying label says
  TO RESET or TO EMPTY, preserving the presenter's forecast decision.
- Unknown deadlines show a dash. Durations above 99 days display >99D;
  normal minute-resolution data is not replaced with a fabricated timestamp.
- The first preview follows supplied fixture minutes. Wall-clock countdown
  integration and post-deadline refresh behaviour remain firmware work.

## Recovery and physical test plan

Confirm the exact unit and revision against the vendor schematic/BSP before
enabling its power rails. Obtain explicit permission for backup and flash.
With the intended unit selected, read the complete detected flash twice and
compare SHA-256 hashes. Keep both the original image and identity/protection
report private. Check secure-boot/flash-encryption status before assuming a
raw backup can be restored. Do not erase NVS or flash when identity or recovery
is uncertain. Record exact bootloader-entry and restore commands for this unit.

Only then prepare a low-brightness static diagnostic using the vendor-native
orientation and paired touch transform. The owner must verify RGB/provider
swatches and independently tap N/E/S/W/C, checking each count. Simulator clicks
do not prove wiring or physical mapping. Quota layout comes after the
diagnostic; motion comes after static physical review and measured stress.

## Remaining work before public support

The normal application build remains blocked: generic settings/Wi-Fi/attention
surfaces have not yet been adapted to the disk. An explicitly selected
`TORGET_ROUND_DIAGNOSTIC=ON` build now uses the 1.75 vendor panel sequence,
GPIO mapping and paired touch transform. It starts at 20% brightness, before
NVS/network initialization, and displays only the five-target static test.
This exception is for hardware diagnosis, not installable VibePulse support. Finish those surfaces, driver/power bring-up, recovery,
touch orientation, service-to-panel/reply verification, full-surface regression
coverage and a named-unit physical report before claiming support.

Publish the verified guide and captures in VibePulse open source, then update
the VibeOnChip presentation with the same support boundary and guide link.
The owner requested both destinations; neither may inherit another board's
physical certification.

## Reusable lessons and native evidence

The owner could read only `1.75`; the registry keeps the unknown PCB revision
explicit. The 480px app contract is centred at (-7, -7), while all visible quota
pixels stay inside the physical 466px disk. Measure number text before placing
its percent unit: reading a lazy x-coordinate on an offscreen page caused an
overlap in the first native draft. No new canvas or full-screen layer is used.

The native preview command checks actual pixels for the circular mask, the
hero/stat gap, readable caption landmarks, quota/today ring partitions, same-data
live/stale provenance and RGB diagnostic swatches. These checks run in the host
gate. The shared presenter tests preserve original adaptive duration strings
and separately check the raw minutes and D:H:M formatter.

![Codex sample, native 466 × 466](img/round-175-codex.png)

![Claude sample, native 466 × 466](img/round-175-claude.png)

These are simulator captures, not photographs of the connected panel.


## Black screen after swapping displays (2026-09-23)

The round unit enumerated over USB but stayed black after software reset and
full USB power cycling. A private flash read found the **1.91 recovery app**:
its complete image SHA-256 matched that task's recorded recovery artifact.
Six older flashing logs concerned the correct 1.91 unit, but did not cover the
later installation. Do not use incomplete logs to dismiss an owner's report.
The app's embedded digest was valid; this was an image for the wrong board,
not evidence that the round panel itself had failed.

A USB path such as `/dev/cu.usbmodem1101` is reusable when boards are swapped.
Before every write, select the USB identity, connect, read and compare the
ROM MAC, and keep that same connection for the write. Keep exclusive serial
ownership through boot verification when multiple tasks are active. Record
board profile, app hash, unit identity and boot result together in private
bench evidence. Publish only sanitized findings. A backup taken now preserves
the current wrong image; it must not be called a factory recovery image.

The diagnostic driver is based on the vendor BSP and schematic at
`waveshareteam/ESP32-S3-Touch-AMOLED-1.75@e4344e70c2fa78a13e8a06566507f1ba8af6672a`.
It uses CO5300 QSPI CS12/CLK38/D4–7, display reset39, gap6/0; CST9217 on
SDA15/SCL14, reset40/IRQ11, native no-swap mirrored X/Y. LCD and touch use
VCC3V3; the vendor display path does not reprogram AXP2101. PCB revision stays
unknown. The vendor-derived panel sequence's Apache-2.0 license is retained
in `components/torget_board/LICENSE.vendor-175`.


## First diagnostic installation

Private double reads of the complete 16 MB flash matched. The authorized
static diagnostic `v1.1.0-17-gf71534f` was installed with all four written
segments hash-verified. Its app file SHA-256 is
`fc1857fea87a1f27afbc4a766d234b3faf0a68462b5b6d89fed9d92fd5dd1960`.
USB boot logs identify the selected 1.75 profile, and CST9217 reports 466 × 466,
chip 0x9217. At the diagnostic screen, the largest internal DMA block was
237,568 B for a 7,456 B transfer; free internal memory was 327,659 B.
These are unloaded diagnostic measurements, not network/TLS stress results.
Owner verification of visible pixels and N/E/S/W/C mapping is pending.
Full VibePulse application support remains unfinished.
