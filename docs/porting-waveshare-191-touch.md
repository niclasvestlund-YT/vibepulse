# Port notes: VibePulse on a 536 × 240 AMOLED

Work started from VibePulse `3ce0e73` on September 23, 2026. The source profile is
`waveshare_191_touch`; the implementation lives in `board_191.c` and shares the
application/platform LVGL code with the simulator and the other board profiles.

## Establish the electrical contract first

The reference is Waveshare's official examples at commit
[`1986f380a4e668da5f4e2b93de9bd82555788018`](https://github.com/waveshareteam/ESP32-S3-AMOLED-1.91/tree/1986f380a4e668da5f4e2b93de9bd82555788018).
The RM67162 uses the SH8601 transport driver. QSPI is 40 MHz on SPI2:
CS6, CLK47, DATA0/1/2/3 = 18/7/48/5, reset17. RGB565, MADCTL 0xF0,
and zero address offsets produce the landscape raster.

FT3168 touch uses the register-compatible FT5x06 driver, address 0x38,
SDA40/SCL39 at 300 kHz. Input is polled. Native portrait coordinates mirror X
before swapping axes; inclusive maxima are 239 and 535. GPIO18 is display DATA0,
so the original board's KEY3 mapping cannot be reused. This profile uses BOOT/GPIO0.
PCB revision has not been established; SD and other peripherals are not claimed.

## Fit the interface, preserve its contracts

The platform presents a centered 480 × 240 content viewport without changing the
existing application API. Board-specific overrides leave the original square and
2.41 layouts intact. Quotas use an 84-pixel IBM Plex font with explicit percent,
period and en-dash glyphs. Reusing the existing numeric font initially produced
missing glyph boxes: native captures exposed the issue before the application flash.

Settings become a two-column grid. Wi-Fi setup puts its QR beside the instructions.
Attention screens put the provider icon beside the text and keep actions on the
bottom row. Render dimensions and prompt-fit checks share constants; long prompts
fall back instead of displaying a truncated decision. The tracker grid and summary
are compressed vertically to leave the pager clear.

The display flush budget is eight rows: 536 × 8 × 2 = 8,576 bytes per transfer.
No additional full-screen persistent canvas was added. Rotation, idle drift and
completion pulses are disabled on this profile pending physical motion review.
OTA is disabled until the update path can verify the new board identity.

## Evidence and reproducible review

1. Read and digest-verified the complete 16 MB factory image before writing.
2. Built the shared native LVGL simulator and captured exact 536 × 240 PNGs.
3. Built/flashed a static diagnostic; esptool verified written segment hashes.
4. Serial startup registered the panel and touch successfully.
5. Owner confirmed all four targets: `1:1 2:1 3:1 4:1`.
6. Built and USB-flashed the normal application; written segment hashes verified.
7. Host regression suite completed successfully; native captures revealed and helped
   fix percent glyph coverage and tracker/footer spacing.

Generate fresh screenshots from source with:

```sh
PYTHON_BIN=python3 tools/preview-ui.sh vibepulse waveshare_191_touch
```

These are simulator fixtures, not photographs or live account readings. Installation
success, network reachability, physical appearance and interaction round trips must
be recorded separately. The current session's final installation report supplies
those results as they become available; do not infer them from a successful build.

## Publishing this material

The installation guide is suitable for the VibePulse open-source docs; this page
provides the engineering material for a VibeOnChip port article. Link a reviewed
source revision when publishing. Keep MAC addresses, local hostnames, Wi-Fi secrets,
private service URLs, device keys, firmware binaries and raw flash backups out of
public artifacts. Preserve the measured geometry, pin table, vendor revision,
commands, fixture captions, observed failure/fix stories and verification limits.

### Post-install touch investigation

The first full application showed recurring FT5x06 I2C read errors despite the successful corner diagnostic. The owner also reported a restart while trying the controls. Wi-Fi setup did open in the serial trace. An experimental correction disables FT3168 automatic monitor mode (register 0x86 = 0) and requests active mode (0xA5 = 0) after driver initialization; physical verification is pending. Register meanings were checked against [LilyGO’s FT3x68 definitions](https://github.com/Xinyuan-LilyGO/T-Connect-Pro/blob/main/libraries/Arduino_DriveBus/src/touch_chip/Arduino_FT3x68.h). No root cause or stability PASS is claimed yet.

Follow-up: active-mode and 100 kHz experiments both retained the periodic I2C read error. The lower-rate build recorded 14 errors in 50 seconds, with no observed panic during that sample. Wi-Fi AP DHCP and two HTTP page requests succeeded. Phone provisioning and sustained touch stability remain open acceptance items.

A real phone attempt exposed a shared onboarding bug: after NO_AP_FOUND (201), the radio retried and obtained DHCP, but the guard retained the earlier error and skipped credential persistence. The fix accepts later IP for the active trial and has regressions for stale IP, failed-start trials and duplicate persistence. See [the lessons entry](lessons.md). USB serial reopening can also reset this unit; use a single persistent monitor while the owner provisions Wi-Fi. First touch-read failure was measured as ESP_ERR_INVALID_STATE after 2 ms, not a multi-second blocked read.

## Final session outcome

Phone onboarding, credential persistence and automatic rejoin passed after the fix. Bounded touch retries recovered transient NACKs in 7 ms in two boot samples, without incrementing the panic counter. See [the final physical report](superpowers/reviews/2026-09-23-waveshare-191-touch.md) for exact firmware and remaining limits; it supersedes earlier pending statuses in this investigation narrative.

Five owner-supplied photographs now document startup and the real Codex weekly
page on this unit. They live in `docs/img/191-touch/glass-*.jpg`; the 9% quota
is a dated account reading, not a fixture. The red ambient light limits color
judgment, and still images do not verify menu navigation or interaction replies.
