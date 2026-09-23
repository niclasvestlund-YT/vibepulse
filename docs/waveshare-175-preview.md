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

## Recovery and physical test plan (not executed)

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

The firmware build is intentionally blocked for this profile: no 1.75 BSP has
been enabled, and generic settings/Wi-Fi/attention surfaces have not yet been
adapted to the disk. Finish those surfaces, driver/power bring-up, recovery,
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
