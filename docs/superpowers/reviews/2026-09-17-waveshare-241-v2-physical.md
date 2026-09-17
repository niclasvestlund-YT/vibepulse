# Waveshare 2.41 V2 — physical installation, 2026-09-17

## Outcome and scope

**PASS for bring-up and a usable VibePulse quota display on the named V2 unit.**
The owner confirmed the diagnostic image and all four touch corners, then
confirmed after the VibePulse installation that usage was visible for Codex
and Claude Code. This is not a physical approval-reply, motion/stress or
long-term reliability certification. No photograph was captured; physical
visual evidence is the owner's direct confirmation.

Unit: `vibepulse-241-v2-01`, owner-confirmed V2 marking. ESP32-S3 revision 0.2,
16 MB flash and 8 MB PSRAM were identified locally. USB installation was on
macOS using ESP-IDF 5.5.2. The existing computer service was left unchanged.
No network names, addresses, credentials, device identifiers or raw logs are
included in this report.

## Diagnostic, backup and final firmware

A full 16,777,216-byte flash backup was saved privately with owner-only access
and verified against the device before any write. Backup and diagnostic flash
had explicit permission. The portrait diagnostic used the vendor V2 pin/reset
sequence, the corrected 16-column gap, RGB/provider-color swatches and four
corner buttons. The owner reported correct image and `TOUCH 4 / 4`.

The separate final VibePulse installation also received explicit permission.
The bootloader, partition table, initial OTA data and application were written;
all four segment hashes verified. Installed build:

- Version: `241-v2-7ca27a2-local`, based on upstream `7ca27a2` plus the local V2 port.
- Application: 2,017,184 bytes; 62% of its 5 MiB slot free.
- App SHA-256: `ddc24f2d88395e26751b86d483ddf7f5025a4c1e8a2d51c76dfd38b9c76d63b5`.
- LVGL 9.5.0; adapter 0.6.4; RM690B0 via SH8601 2.0.1~1;
  FT5x06 1.1.0~1; TCA9554 2.0.3.
- Fixed landscape 600 × 450; BOOT/GPIO0 settings input.
- No compiled Wi-Fi password, interaction key or OTA key; local provisioning.

This identifies the actual flashed image even though its source was not yet
committed. Later documentation, build-selector, preview and CI additions do
not imply that their exact future commit binary was flashed.

## Observations

The serial boot identified the expected app version and V2 geometry, registered
touch in IRQ mode and started LVGL. A sampled largest internal DMA block was
69,632 bytes against a 9,600-byte transfer requirement. Wi-Fi setup, settings
and OTA UI creation each showed zero additional internal heap cost in those
startup measurements; their LVGL allocations remained in the PSRAM pool.

The first log recorded boot #1 after flash. Reopening the serial connection
correlated with a second `USB_UART_CHIP_RESET`; the reboot ledger remained at
zero panic, watchdog and brownout resets. Logging was then stopped rather
than risking interruptions to phone provisioning. Deasserting DTR/RTS in the
read script was not sufficient to establish passive observation on this setup.

The owner subsequently reported that the panel worked and displayed Codex
usage and Claude Code usage. This establishes network-fed values visible on
the glass. It does not prove that every displayed provider reading was fresh:
the computer diagnostics earlier reported an expired saved Claude credential
and stale Claude week fields, while Codex week data were fresh. Cancellation
of a subscription and presence of a cached quota display are separate facts;
no billing or entitlement conclusion was drawn.

## Automated evidence before installation

- Shared LVGL native previews: 154 frames at 600 × 450, including live/stale/
  missing data, wide values, activity, attention, settings, Labs and Wi-Fi.
- Exact footer edge clearance and provider accents checked; important states
  visually inspected. README pictures are fixture renders, not panel photos.
- Original 480 × 480 preview passed; original-board driver/main/rotation C
  compilation paths passed after introducing the board wrapper.
- Full `test/run.sh` passed, including 927 tokenserver tests, C/policy suites,
  Python layout/privacy/configuration tests, both Worker suites and TypeScript.
- ESP32-S3 target build passed. Optional-disabled and vendor-library warnings
  were present; this report does not claim a warning-free build.

## Not separately exercised

- A four-corner sweep in final landscape orientation, timed BOOT gestures,
  every Labs/maintenance control, or measured swipe/animation latency.
- Twenty repetitions under network/TLS stress, long soak, and power-cycle
  persistence of the provisioned network.
- Physical interaction replies, E2E relays, OTA update/recovery, IMU rotation,
  audio or other optional peripherals.

The V1 board and another V2 unit do not inherit this physical report.
