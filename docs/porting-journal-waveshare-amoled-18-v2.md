# Porting journal: Waveshare ESP32-S3-Touch-AMOLED-1.8 V2

This is the working engineering record for bringing VibePulse to the Waveshare
ESP32-S3-Touch-AMOLED-1.8 V2. Keep observations, failed approaches, commands,
source revisions, corrections, lessons and the evidence behind this board's
support boundary. The journal is not a flash authorization.

## Target and evidence boundary

- Board: Waveshare ESP32-S3-Touch-AMOLED-1.8, V2 confirmed by the owner from
  the label on the back of the board.
- Waveshare identifies V2 as a CO5300 QSPI AMOLED with CST820 I2C capacitive
  touch. Its stated native raster is 368 x 448 portrait.
- Waveshare documents ESP32-S3R8, 8 MB PSRAM, 16 MB flash, AXP2101 PMU,
  PCF85063 RTC, QMI8658 IMU, ES8311 audio, microphone, speaker, and microSD.
  Those are board/document facts only; VibePulse support and physical operation
  for them have not been established.
- At session start macOS listed an Espressif USB JTAG/serial interface. This
  transient host observation is not proof of the board revision or a durable
  unit identity; the port name is omitted from the journal.
- No serial session was opened. Existing porting evidence says opening serial
  can reset the connected panel on this host; passive observation is not
  assumed.
- The owner authorized flashing and later reported the display, touch, Wi-Fi
  data path and BOOT settings working. The physical sequence and its limits
  are recorded below; do not infer runtime memory or unsupported peripherals.

## 2026-09-25 — intake and source inspection

1. Located the VibePulse firmware checkout at
   `work/vibepulse` in the existing VibePulse workspace. Its starting revision
   was `90a677a` (`main`, clean); work continues on
   `codex/waveshare-amoled-18-v2`.
2. Asked the owner to read the board's rear label because Waveshare ships two
   incompatible display/touch combinations under the same 1.8-inch product
   name. The owner confirmed **V2**. This removes the V1 SH8601/FT3168 profile
   from scope.
3. Read the official Waveshare 1.8-inch product page and documentation, plus
   the official examples repository. The repository checked out for source
   comparison is `waveshareteam/ESP32-S3-Touch-AMOLED-1.8` at commit
   `78e13f852929c2ab4f9d5e0ad1c50ea378dbf2b4`. It documents both revisions and
   uses the managed `waveshare/esp32_s3_touch_amoled_1_8` BSP. The upstream V2
   firmware manifest requests BSP `2.0.3`.
4. Read `docs/adding-a-display.md`, root `AGENTS.md`, the existing board
   selector, board wrapper, shared display geometry, and 2.41 V2 guide/report.
   The existing design is mostly laid out on a 480 x 480 logical canvas. The
   new 368 x 448 physical raster is portrait and significantly narrower, so
   a display driver alone will not establish a usable port. Screen fit and
   touch alignment need their own native-size review.
5. The Component Registry page for BSP 2.0.3 lists the CO5300 driver but calls
   the supported touch family CST816S while the board's V2 label says CST820.
   The official examples repo nevertheless selects BSP 2.0.3 for its V2
   firmware package. Keep this as a specific verification item: inspect the
   exact resolved component, then require a four-corner physical touch sweep.
   The product documentation also retains an older FT3168 paragraph in its
   generic touch section, so revision-specific statements take precedence over
   that unqualified paragraph.
6. Inspected the resolved BSP `2.0.3` source before `idf.py fullclean` removed
   the generated component checkout. `bsp_display_new()` selects the CO5300
   QSPI panel. Brightness uses the panel command. The board header specifies
   I2C SDA 15/SCL 14 and touch IRQ 21; panel reset, touch reset, and panel
   backlight are `GPIO_NUM_NC`. The touch helper probes the CST816S address,
   then FT5x06, and applies an X gap of `0x10` only on the CST816S path. V2's
   printed controller is CST820, so this source inspection does not prove
   touch-driver compatibility or coordinate correctness. The selected native
   touch transforms are all zero and still need the four-corner sweep.
7. Added the explicit `waveshare_18_v2` selector, BSP wrapper, BOOT/GPIO0
   settings mapping, 368 x 448 raster, 12-row (8,832-byte) flush, simulator
   selector, and hardware registry. Touch uses the 1.8 BSP's
   `bsp_touch_config_t`; the 2.16 BSP's `bsp_display_cfg_t.touch_flags` is not
   part of this API. Fixed-orientation profiles also skip the auto-rotation
   task, which otherwise referenced a display rotation routine intentionally
   excluded from the 1.8 profile.
8. Ran `tools/preview-ui.sh vibepulse waveshare_18_v2`. Its first 137 native
   captures exposed severe clipping from the centred 480 x 480 viewport. I
   replaced that crop with a board-profiled LVGL transform, pivoted at the
   origin and scaled to 368 x 448 (scale 196/239 in LVGL's 256-based units).
   The second 137-capture run shows the selected usage, NEEDS YOU, settings,
   and boot surfaces within the glass. Representative captures are preserved
   in the task output directory. The transform allocates a temporary
   composition layer; use a separate 768 KiB PSRAM-backed LVGL pool for V2.
   This estimate still needs a target runtime high-water measurement.
9. The first clean bootloader build appeared to overflow `.iram.text` by 503
   bytes and reported missing `call_start_cpu0` plus unresolved `end`. The
   underlying cause was the bootloader's separate CMake build using macOS
   `/usr/bin/ar` and `/usr/bin/ranlib` for Xtensa archives. The host ranlib
   emitted empty archive indexes, so the link skipped the ESP-IDF entry object.
   Added `cmake/xtensa-archive-tools.cmake` and passed it as
   `CMAKE_USER_MAKE_RULES_OVERRIDE_C` to the external bootloader project. A
   clean reconfigure confirmed the generated C compiler rules now use
   `xtensa-esp32s3-elf-ar`; the bootloader linked successfully. Removed the
   temporary linker-flag experiments after the root cause was confirmed.
10. The clean build then exposed a stale generic `tg_board_touch_new()` wrapper:
    it passed `bsp_display_cfg_t` where the installed BSP API requires
    `bsp_touch_config_t`. Changed the generic wrapper to use the BSP touch
    config. The separate 1.8 V2 branch already uses the correct type.
11. The full build initially entered the machine-local Solelkollen checkout
    and failed because its `net.c` references an undefined `SG_GLANCE_URL`.
    Kept that unrelated external source untouched and rebuilt with
    `-DTORGET_SOLELKOLLEN_DIR=/tmp/torget-no-solelkollen`. Before flashing,
    checking `CMakeCache.txt` caught that the first successful build still had
    the default `waveshare_216` profile; no flash command had reached the
    device. Reconfigured explicitly with `-DTORGET_BOARD=waveshare_18_v2` and
    a separate generated `sdkconfig.waveshare_18_v2`, then rebuilt all objects.
    The correct ESP-IDF 5.5.2 V2 firmware is `build-18-v2/torget.bin`, size
    0x1F01D0 bytes, leaving 0x30FE30 bytes (about 61%) in the 5 MiB OTA app
    slot. Its bootloader is 0x4F10 bytes, leaving 38% of the bootloader slot.
    The build reports duplicate Waveshare BSP Kconfig symbols because both
    1.8 and 2.16 managed components are present; they are warnings, not build
    failures. The registry validator reports
    `OK: 4 capabilities, 4 sources, 1 units`.
12. Space recovery removed only this task's 670 MB upstream clone, 292 MB
    simulator build cache, and 102 MB temporary capture set. Four reviewed
    representative simulator PNGs were retained in the task outputs. The
    damaged managed LVGL copy was restored from Espressif's local cache.
13. The owner explicitly approved flashing and said the connected device held
    only standard data, so no full-flash backup was taken. A profile check
    caught and prevented an earlier `waveshare_216` image from being written.
    After the owner entered ROM download mode with BOOT, flashed the correct
    V2 build using esptool `--verify`: bootloader at `0x0`, partition table at
    `0x8000`, OTA data at `0x19000`, and app at `0x20000`. All four segments
    reported `Hash of data verified`; esptool then reset the board. NVS was not
    erased. The initial report after reset was a black screen, but the owner
    then confirmed VibePulse appeared and supplied two photographs of the
    running portrait display. The owner now reports display, touch, Wi-Fi data
    path and BOOT settings working. See the physical review for which parts are
    owner-reported versus directly visible in photos. iPhone Wi-Fi onboarding
    remains cumbersome and is a separate product UX issue.

### Port completion and lessons

The owner confirmed the board works after the correct profile was installed.
Public support is limited to this rear-label-confirmed V2 unit and its
display, touch, Wi-Fi/data path and BOOT settings. The two photographs show
only the live display; the remaining checks are explicitly attributed to the
owner. No inference is made about the listed audio, RTC, IMU, PMU or microSD.

Lessons to carry into VibeOnChip and the next display bring-up:

- Ask for the board's rear-label revision before selecting a driver. The 1.8
  retail name covers incompatible V1 and V2 glass/touch combinations.
- Verify the generated build cache's `TORGET_BOARD` before any flash. The
  first successful compile had silently kept the default 2.16 profile; this
  was caught before it reached the board.
- Rebuild the bootloader too. macOS host `ar`/`ranlib` emitted Xtensa archives
  without the indexes expected by ESP-IDF, so the external bootloader needed
  explicit Xtensa archive tools.
- Match each board's BSP API exactly. The 1.8 touch factory takes
  `bsp_touch_config_t`, unlike the older generic path's display config.
- Native 368 × 448 previews exposed clipping that square simulator frames
  could not show. Inspect all representative surfaces at actual panel size.
- Restrict parallel compilation on small-memory machines and exclude unrelated
  machine-local companion projects from a VibePulse-only build.
- Track chip facts, enabled firmware, per-unit physical evidence and untested
  peripherals separately. A successful flash hash proves the write, not that
  the display, touch or network behavior works.
- The iPhone flow needs product work: a QR can identify the panel's temporary
  access point, but it does not remove the user's OS network-join decision or
  captive-portal navigation. Do not call this path effortless because QR is
  present.

### Remaining measurements and follow-up

- Measure LVGL pool high-water and internal DMA headroom on a running unit;
  compile-time pool sizing is not a runtime measurement.
- Improve Wi-Fi onboarding, especially the multi-step iPhone join and portal
  return. Keep explicit iOS consent visible and do not promise silent joins.
- Consider isolating the unrelated 2.16 BSP Kconfig symbols so a clean build
  does not print duplicate-symbol warnings; they did not prevent this build.
- Future unit writes still require an explicit authorization and backup
  decision. Untested audio, IMU, RTC, battery, microSD, OTA, motion and Windows
  claims remain explicitly unverified.

### Session stopping point

The board selector, BSP wrapper, registry, simulator geometry, native
previews, support guide, physical review, and process journal are complete. The clean
VibePulse-only firmware and bootloader build with `waveshare_18_v2`; the app
fits its OTA partition with about 61% free. The correct image is now installed
and esptool verified each written segment. No backup was taken per the owner's
instruction that the unit held only standard data; NVS was preserved. The
owner reports display, touch, Wi-Fi/data path and settings working, and the
photos show the live screen. Peripheral compatibility remains unverified.
VibeOnChip publication is prepared separately from the existing 1.91 branch.

## VibeOnChip devlog handoff

The reader-facing VibeOnChip entry should explain why the
same product label needed a revision check, how the narrower portrait canvas
changed the UI, which visual/touch issues were found and fixed, and what was
actually shown on the named unit. Use only owner-supplied non-sensitive photos
and sanitized results. Do not publish serial identifiers, logs, network
details, flash backups, credentials, or unverified feature claims. Preserve
the iPhone Wi-Fi onboarding friction as an explicit next improvement.

## Source links

- [Waveshare product documentation](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-1.8)
- [Waveshare source examples](https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-1.8)
- [Waveshare schematic](https://files.waveshare.com/wiki/ESP32-S3-Touch-AMOLED-1.8/ESP32-S3-Touch-AMOLED-1.8-Schematic.pdf)
