# Adding another display to VibePulse

A successful ESP32-S3 compile is not a board port. Identify the **model and
PCB revision**, wire up its actual peripherals, inspect native pixels and
record evidence on one named unit before announcing support.

## Existing profiles and evidence

| CMake `TORGET_BOARD` | Physical raster | Settings input | Hardware registry |
|---|---|---|---|
| `waveshare_216` (default) | 480 × 480 | KEY3 / GPIO18 | `spec/` |
| `waveshare_241_v2` | 600 × 450 landscape | BOOT / GPIO0 | `spec/boards/waveshare_241_v2/` |

Each registry contains `hardware.md`, `hardware-capabilities.yaml`,
`hardware-sources.yaml`, `device-units.yaml` and `hardware-opportunities.md`.
The same existing registry validator accepts either directory. A new board
gets its own evidence; leave existing units' installed versions and verified
capabilities unchanged.

## Repeatable workflow

1. **Identify.** Obtain the model/revision marking. Enumerate USB devices and
   select the intended unit; several ESP32 displays can share a computer.
   Do not infer revision from the screen size, USB product name or chip ID.
2. **Read primary sources.** Inspect vendor docs, schematics and exact example
   revisions. Compare factory, Arduino and ESP-IDF implementations where they
   disagree. Inventory panel bus/pins, reset/power expander, touch bus/IRQ,
   buttons, flash and PSRAM. A controller can legitimately use a differently
   named vendor transport driver, as RM690B0 does with SH8601 here.
3. **Separate evidence.** Distinguish SoC capability, board wiring, enabled
   firmware and physical verification. A successful screen says nothing about
   a speaker, IMU calibration, battery or every other ESP32 feature.
4. **Preserve recovery.** Obtain explicit backup/flash authorization. Read and
   verify the full flash before overwriting it; keep it private. Identify the
   bootloader entry sequence and record the actual partition/flash arguments.
   Do not erase NVS to solve a host build-configuration error.
5. **Bring up a static diagnostic.** Start with the vendor's native orientation,
   low brightness, black background, RGB/provider-color swatches and four
   uniquely counted touch corners. Preview that exact UI before flashing.
   The owner must inspect real pixels and touch every corner. A simulator's
   synthetic click does not test touch wiring or coordinate transforms.
6. **Implement the board boundary.** Add the explicit profile to
   `cmake/torget_board.cmake`, `components/torget_board/` and
   `platform/display_geometry.h`. Keep panel MADCTL, address gaps and touch
   transforms together. Do not turn another board's GPIO18 into a button or
   apply its IMU calibration. Unknown profile names must fail early.
7. **Budget memory at the new width.** Flush bytes are width × rows × bytes per
   pixel. V2 uses 600 × 8 × 2 = 9,600 B, below the original 11,520 B. Keep
   two-pixel dirty-area alignment and sample the largest internal DMA block;
   free PSRAM and summed low-water heap are not substitutes. Extra persistent
   layers or larger buffers require measured budgets and the AMOLED workflow.
8. **Fit every surface.** Physical raster and app composition are separate.
   Preserve native fonts and bitmaps. Inspect live/stale/no-data, widest copy,
   attention, pager, completion borders, settings, Labs, boot and Wi-Fi/QR
   surfaces at the real dimensions. For V2, a centred 480-pixel composition
   loses 15 pixels of vertical margin per side; footers and frames therefore
   needed explicit adjustments. Do not silently change the public app API.
9. **Build reproducibly.** Pin target and simulator LVGL to the same version.
   Keep board-specific build and generated SDK-config files. Configure once,
   then use `cmake --build <build-dir> --parallel 2` on small-memory machines.
   A version range can resolve newer libraries; defaults do not migrate an
   old generated SDK config when switching versions back.
10. **Check and install.** Run the complete host suite, native preview and each
    supported firmware build. Get explicit permission for the final install.
    Verify all written hashes, boot identity and physical static operation.
    Provision Wi-Fi locally if no credential is available. Confirm the actual
    service-to-panel path; a working radio alone does not establish it.
11. **Publish the support boundary.** Add the registry, sanitized physical
    report, setup guide, native fixture captures, README row, changelog and CI
    coverage in one PR. State the exact tested build and untested features.
    Do not publish credentials, personalized binaries or flash backups.

## Traps from the first 2.41 V2 port

- V1/V2 reset wiring differs. Size and chip family were insufficient selectors.
- One vendor LVGL demo omitted the panel's 16-pixel address gap. Factory and
  Arduino sources supplied it. After rotation the gap moved to the other axis.
- Native touch coordinates remained portrait: mirror Y, then swap XY for the
  selected landscape MADCTL. Rotating only the picture was not enough.
- Original KEY3/GPIO18 is an expander interrupt on V2. BOOT/GPIO0 became the
  settings input; button hints and boot-strap instructions had to change too.
- The first landscape images exposed footer/frame overlap hidden by the
  480 × 480 preview. Exact native captures caught it before installation.
- `lvgl: 9.*` selected 9.6 while the simulator used 9.5. Reusing that generated
  configuration on 9.5 then left an empty assert include. Pinning versions and
  regenerating the board config resolved the build failure.
- Many concurrent C++ compilations caused memory/swap pressure on an 8 GB Mac.
  A two-worker build completed with substantially less pressure.
- A converter inferred companion apps from the home directory although the
  simulator had been built without them. Expected capture sets now use the
  actual CMake cache rather than unrelated folders on the host.
- An existing `secrets.h` had empty network fields. Network settings learned
  by a panel live in its NVS; they are not automatically present on the Mac.
  Failed keychain retrieval must not be treated as an open network. The phone
  portal was the successful fallback.
- Reopening serial correlated with a USB reset despite deasserted DTR/RTS.
  A read-only intention is not proof of passive observation. Record reset
  causes and stop repeated port opens during provisioning; no universal
  no-reset serial recipe was verified in this session.
- Visible quota values and provider freshness are distinct. A stale source or
  cancelled subscription is not evidence of a broken display driver.

Do not inherit motion, OTA, Windows physical-loop or long-soak approval from
another model. Board-safe OTA identification is follow-up work; the V2 guide
currently specifies USB updates. New animation work follows the separate
physical performance protocol in the AMOLED skill.
