# VibePulse Studio, Hardware Radar, and Wireless Delivery

## Outcome

Turn Torget development into a fast, hardware-aware loop in which Codex and
Claude Code share the same facts, explore VibePulse at its real 480 x 480 size,
and install approved firmware over Wi-Fi with automatic recovery.

The system must make relevant hardware opportunities visible without requiring
Niclas to remember or repeat that the device has Bluetooth LE, microphones,
audio output, motion sensing, battery support, an RTC, microSD, native USB, and
other resources. It must also prevent an agent from confusing a feature of the
ESP32-S3 chip with a feature that is wired, supported, enabled, and verified on
this particular board.

This specification extends the fast visual loop in
`2026-08-10-fast-esp32-amoled-design-loop.md`. It deliberately separates four
delivery tracks so each can be implemented and verified independently:

1. the hardware capability knowledge system;
2. the VibePulse Studio visual preview;
3. safe over-the-air firmware infrastructure;
4. the Studio's wireless install integration.

## Scope boundaries

This design covers the shared knowledge architecture and the intended boundary
between VibePulse Studio and the device. It defines safe OTA behavior in enough
detail to constrain its later implementation.

It does not implement every latent hardware feature. Voice interaction,
battery operation, Bluetooth product features, microSD history, USB host mode,
and new gesture systems remain separate features that require their own small
design and physical verification before implementation.

Production hardening is also separate from the first personal-development OTA
release. No task may burn security eFuses merely because OTA is being added.

## Source hierarchy and truth model

Hardware sources disagree. The knowledge system must retain those
disagreements rather than silently choosing the most convenient claim.

Use this evidence order:

1. observation or measurement on a named physical Torget unit;
2. the exact board revision's schematic;
3. source code of the BSP and driver version locked by Torget;
4. the official Waveshare board documentation and first-party examples;
5. the Espressif ESP32-S3 and ESP-IDF documentation;
6. community sources, clearly marked as unverified leads only.

A lower-ranked source cannot overwrite a higher-ranked physical finding. A
newer vendor document can open a discrepancy without erasing the working fact.

Each capability records these states separately:

- `soc_capable`: the ESP32-S3 silicon supports it;
- `board_wired`: this board exposes or connects the required hardware;
- `bsp_support`: the locked BSP or another pinned driver supports it;
- `firmware_enabled`: the current Torget configuration enables it;
- `unit_verified`: it has passed a named physical test on the actual unit;
- `confidence`: measured, schematic, source-inspected, vendor-claimed, or
  unverified;
- `constraints`: electrical, memory, radio, bus, lifecycle, or UX limits;
- `conflicts`: features or debug modes that cannot safely run together;
- `opportunities`: product ideas made possible by the capability;
- `sources` and `last_verified`: traceable evidence and freshness.

No single boolean named `supported` is allowed because it would collapse these
essential distinctions.

## Repository architecture

Keep knowledge in Torget so it is versioned with the firmware and travels to
every workstation.

### `spec/hardware.md`

Retain this path as the concise human entry point. Expand it with a capability
summary, source hierarchy, known traps, and links to the structured registry.
Do not duplicate the entire registry in prose.

### `spec/hardware-capabilities.yaml`

This is the canonical machine-readable capability registry. Every entry uses
the truth model above and includes resource ownership, known pin/bus use,
source identifiers, and current Torget status.

The registry must remain easy to review by hand. A small schema validator must
reject missing evidence, invalid state names, unknown source identifiers,
duplicate capability IDs, and claims marked physically verified without a unit
and test reference.

### `spec/hardware-sources.yaml`

This is the canonical source ledger referenced by capability entries. Each
source gets a stable ID plus its title, publisher, URL or repository path,
document or code revision, accessed date, applicable board revision, and source
rank. Local physical evidence links to the device unit and a reproducible test
record instead of pretending to be an external document.

The schema validator must reject unknown source IDs and incomplete citations.
Updating a URL or access date must not silently change the technical claim or
its confidence; changed evidence requires an explicit registry review.

### `spec/hardware-opportunities.md`

Keep product ideas separate from hardware facts. Each opportunity links to the
capabilities it needs and states prerequisites, conflicts, expected user value,
physical work, privacy implications, and a status such as `idea`, `candidate`,
`designed`, `implemented`, or `verified`.

This is the file agents consult when a request creates an opportunity to reuse
existing hardware. It is not an automatic backlog and does not authorize
unrequested implementation.

### `spec/device-units.yaml`

Record each physical device without secrets. A unit entry includes a friendly
name, board/SKU/revision evidence, enclosure, attached speaker, battery,
microSD, antenna configuration, installed firmware identifier, and the last
physical verification date. Wi-Fi credentials, OTA keys, access tokens, and
other secrets never belong here.

### Agent discovery

`AGENTS.md` and a thin root `CLAUDE.md` must both point to the same canonical
files and contain the same behavioral rule:

> Before proposing external hardware, declaring a device limitation, or
> designing a hardware-dependent feature, consult the capability registry and
> opportunity radar. State whether the feature is merely silicon-capable,
> board-wired, enabled, and physically verified. Mention a relevant unused
> onboard capability when it materially improves the user's request.

Do not duplicate hardware facts inside agent instruction files or skills. A
shared skill may route agents to the registry, but the skill must not become a
second source of truth.

## Initial critical capability audit

The first registry population must include at least the following findings.

### Processing and memory

The board is documented as an ESP32-S3R8 running up to 240 MHz with 8 MB of
octal PSRAM and 16 MB NOR flash. Torget currently uses the dual-core ESP32-S3
and PSRAM, while the application image is about 1.6 MB.

PSRAM is capacity, not universal RAM. Flash writes can make PSRAM unavailable,
DMA descriptors still require suitable internal memory, and large simultaneous
display, TLS, Wi-Fi, Bluetooth, and audio allocations can starve internal RAM.
Every large or DMA-related allocation therefore needs a measured memory budget.

### AMOLED and touch

The display is a 2.16-inch, 480 x 480 CO5300 AMOLED over 40 MHz QSPI, using
RGB565 with big-endian byte order. Torget's verified starting point is a
480 x 50 partial double buffer in PSRAM and the existing two-pixel dirty-area
alignment rule.

AMOLED lifecycle protection is a product capability requirement, not merely a
visual preference. Static interfaces must use predominantly black backgrounds,
automatic dimming and display-off behavior. Small pixel shifts may be evaluated
later, but cannot move critical content out of alignment or reduce legibility.
The physical AMOLED review remains mandatory immediately after an approved
static layout and before animation tuning.

The current source set conflicts over the touch controller name: current
Waveshare material says CST9220, while the locked BSP instantiates the CST9217
driver. The working driver is the operative fact until a physical chip/revision
check proves otherwise. It currently exposes one touch point, so ordinary
single-finger taps and swipes are supported but multi-touch is not promised.

### Wi-Fi and Bluetooth

The board has 2.4 GHz 802.11 b/g/n Wi-Fi and Bluetooth 5 Low Energy. It does not
have 5 GHz Wi-Fi or Bluetooth Classic. The ESP32-S3 can run station, SoftAP, or
station plus SoftAP modes, enabling home Wi-Fi, phone-hotspot use, and a direct
maintenance network.

Wi-Fi and BLE time-share the same radio. Enabling BLE changes binary size,
internal-memory pressure, power use, and high-traffic behavior. BLE must not be
added as a harmless background checkbox. Torget currently enables Wi-Fi and
disables Bluetooth.

Useful candidates include BLE or SoftAP provisioning, local device discovery,
proximity features, and low-bandwidth control. Normal development OTA should
use Wi-Fi because firmware images are too large for BLE to be the preferred
transport.

### Audio

The board carries an ES7210 microphone ADC, dual microphones, an ES8311 playback
codec, I2S wiring, amplifier control, and speaker output. The schematic includes
an acoustic echo-reference path, but this is not evidence that a complete
software echo-cancellation algorithm runs automatically.

The physical unit inventory must say whether a speaker is actually fitted or
attached. Microphone and speaker support must be tested independently and then
in full duplex. Audio also competes for I2S, DMA, internal RAM, CPU time, and
possibly the native USB stack used by Buddy. Privacy-sensitive recording must
have an unmistakable visible state and a hardware-accessible stop action.

Potential uses include completion sounds, spoken alerts, voice activity
detection, local wake-word experiments, voice control, and audio diagnostics.
No independent buzzer or vibration motor is documented on the board. A
completion sound therefore depends on a physically verified speaker path, and
haptic feedback would require external hardware.

### IMU and interaction

QMI8658 provides a three-axis accelerometer plus three-axis gyroscope: six
physical axes, not nine without an additional magnetometer or derived sensor
fusion. Torget has physically verified rotation using the device at I2C address
`0x6B`, despite inconsistencies in component headers.

Future opportunities include tap, shake, lift-to-wake, orientation-aware
animation, and desk-presence behavior. Each gesture requires false-positive
testing on the physical enclosure; marketing copy alone does not prove a
reliable product gesture.

### Power, battery, and RTC

AXP2101 manages power and supports USB/battery detection, voltage and percentage
measurements, charging state, power-key events, and programmable power behavior.
The board exposes a two-pin connector for a 3.7 V lithium battery but does not
imply that every Torget unit contains one.

Battery enablement requires the actual cell capacity, connector polarity,
allowed charge current, thermal design, enclosure, and shutdown behavior to be
verified. Waveshare's example configures a 400 mA charge current and disables
the absent battery-temperature sensing path; those settings cannot be copied
blindly to an unknown battery.

The PCF85063ATL RTC and its interrupt line can support persistent time, alarms,
scheduled wakes, and network-independent reset timing. Presence in the
schematic is not yet a Torget physical verification. Sleep and wake behavior
must account for display rails, Wi-Fi reconnect time, and the difference
between light sleep, deep sleep, and PMU power-off.

Current Torget source comments treat the RTC as not battery-backed. Preserve
that as current implementation evidence, while keeping physical verification
separate, until the exact unit and board revision prove otherwise. No feature
may promise correct time through a complete power loss before that verification.

### Storage

The TF/microSD slot is physically present. The current BSP mounts it as a
one-bit SDMMC device even though some vendor text calls the interface SD SPI.
There is no card-detect or write-protect signal in the current BSP, so removal
and write interruption must be handled defensively.

Possible uses include long-term usage history, offline assets, audio files,
diagnostic bundles, and recovery exports. Firmware correctness and essential
settings must not depend on a removable card.

### USB and physical controls

ESP32-S3 supports native USB device functions and USB host/OTG at the silicon
level. The board wires D- and D+ to GPIO19 and GPIO20 through its USB-C port.
USB device classes can include CDC, HID, MIDI, mass storage, audio, or composite
devices, subject to endpoint and memory limits.

USB host mode is not declared board-ready merely because the SoC supports it.
Safe host operation needs correct VBUS sourcing, current limiting, connector
role behavior, and a plan for the single internal USB PHY shared with USB
Serial/JTAG. Buddy's native-USB design and the USB debugging path must be
treated as resource conflicts.

GPIO18 is an active-low user button. BOOT and power buttons have boot/PMU roles
and cannot be treated as ordinary controls without preserving recovery. A later
button mapping may provide navigation, maintenance mode, rollback, or dismissal,
but the emergency recovery gesture must remain available.

### Antenna and expansion

The onboard antenna supports the shared 2.4 GHz Wi-Fi/BLE radio. The IPEX
connector is an advanced hardware modification that requires resistor changes;
it is not a runtime setting. The shared I2C bus already carries touch, PMU, IMU,
RTC, and audio control, so any external I2C proposal must check address, voltage,
bus speed, locking, and interrupt availability.

No ambient-light sensor is documented. Automatic brightness can initially use
time, touch/activity, agent state, and power state, but true room-light response
would require an externally verified sensor.

### Other silicon capabilities

ESP32-S3 also contains capabilities such as the ULP coprocessor, hardware
cryptography and random-number generation, ADC, an on-chip temperature sensor,
capacitive-touch IO, PWM, RMT, and TWAI. They are candidates for the registry,
not automatic board features: pins may be occupied, unexposed, electrically
unsuitable, or unsupported by the current firmware.

Only add these to the opportunity radar when they materially fit a product
idea, and record the required pin/resource audit. In particular, the on-chip
temperature sensor measures silicon temperature and must not be presented as a
reliable room-temperature sensor.

### Security and recovery

ESP32-S3 offers secure boot, flash encryption, encrypted NVS, signed
applications, hardware-backed identity primitives, and OTA rollback. Torget
currently has secure boot, flash encryption, Bluetooth, dynamic power
management, OTA partitions, and bootloader application rollback disabled.

This current-state record is essential. A silicon capability is not an enabled
protection, and irreversible production security settings must never be inferred
from the presence of a Kconfig option.

## Safe wireless delivery design

### Partition and bootstrap boundary

The current partition table contains one factory application and explicitly
disallows OTA. Enabling safe OTA therefore requires one final full USB flash to
install an OTA-aware bootloader, partition table, and application.

The new partition table must contain `otadata` and at least `ota_0` and `ota_1`
application slots. Slot sizes must be derived from the measured application
size plus documented growth headroom for fonts, TLS, audio, and future apps.
Normal OTA is allowed to update only the inactive application slot. Bootloader,
partition table, NVS layout, and arbitrary data partitions remain USB/recovery
operations unless a separate migration is explicitly designed.

### Development install flow

1. Studio performs the normal incremental ESP-IDF build.
2. It discovers a device using mDNS, remembered IP, or an explicitly entered
   address. Discovery failure must not select another device silently.
3. Studio reads device identity, board compatibility, running version, active
   partition, free update capacity, and update authorization state.
4. The user explicitly selects `Install wirelessly` for the named unit.
5. The device accepts an authenticated firmware upload only while local OTA is
   enabled, validates project/chip metadata, image structure, size, and digest,
   and writes to the inactive slot.
6. The display shows a minimal, large update state and progress without heavy
   transforms or animations.
7. After complete verification the device selects the new slot and reboots.
8. Studio reconnects, confirms the expected firmware identity, and reports
   success or rollback. A lost connection is not reported as success.

Home Wi-Fi and a phone hotspot are first-class paths. Because hotspot mDNS and
client-to-client behavior can vary, a deliberately entered IP and a temporary
`VibePulse-Update` maintenance SoftAP at a documented address are required
fallbacks. The maintenance network must time out and must not remain exposed
during normal operation.

### First-boot health and rollback

Enable ESP-IDF application rollback. A newly installed image remains pending
until a short deterministic self-test confirms the core runtime rather than an
external service:

- firmware metadata and essential NVS can be read;
- PSRAM and required internal-memory reserves are healthy;
- display/LVGL initialization completed;
- the UI and platform scheduler each produced a heartbeat;
- no watchdog, panic, or reset loop occurred during the verification window.

Internet access, Claude/Codex servers, Solelkollen data, home Wi-Fi, or a
particular hotspot must not be required to mark structurally healthy firmware
valid. Those dependencies may be temporarily unavailable while the firmware is
correct. If the core self-test fails or the unit reboots before confirmation,
the bootloader returns to the previous working OTA image.

A documented physical maintenance gesture must expose version, reset reason,
network address, OTA state, and rollback/recovery options. USB remains the final
recovery path.

### Security phases

Personal development starts with the smallest safe reversible system:
authenticated local maintenance mode, one-time or device-specific authorization,
strict image validation, SHA-256 identity, A/B slots, rollback, short exposure
windows, and no anonymous permanent upload endpoint.

Before distribution to other users, add HTTPS server verification, signed
firmware, protected provisioning, encrypted NVS, unique device identity, and a
tested key-rotation and recovery process. Secure Boot v2 and flash encryption
are production decisions performed only through a separately reviewed workflow
because eFuse changes can be irreversible and can disable familiar recovery
paths.

Compiled `secrets.h` values are not a production provisioning system and must
never be copied into the capability registry, Studio project files, screenshots,
logs, or device inventory.

## VibePulse Studio integration

The Studio remains VibePulse-focused in its first release. It renders exact
480 x 480 screens using the real fonts, provider colors, spacing, states, and
content constraints. It saves the approved design as versionable JSON and
exports exact 480 x 480 PNG references plus LVGL-oriented layout, color, and
typography constants. The firmware remains the runtime source of truth; Studio
output is an explicit reviewed handoff, not an opaque code generator.

The hardware registry supplies immutable device facts such as resolution,
color format, safe-area assumptions, input model, AMOLED constraints, and OTA
availability. Studio must not copy those facts into a second manual config.

The wireless-install panel is unavailable until the OTA foundation has passed
its independent physical gate. Preview and install are separate actions:
approving a mockup never authorizes a flash, and connecting to a device never
changes it.

The wireless-install milestone must show the selected unit's firmware version,
IP/RSSI, active slot, previous rollback, reset reason, uptime, free internal
heap, free PSRAM, and build identity. These diagnostics are required acceptance
criteria, not optional decorative Studio features.

## Maintenance rules

- Record source title, URL or repository revision, accessed date, and relevant
  version for every external claim.
- Review the capability registry whenever the Waveshare BSP, ESP-IDF, LVGL,
  board revision, partition table, or enclosure changes.
- Update `unit_verified` only after a named physical test; simulator success is
  not hardware verification.
- Preserve conflicting evidence until the conflict is resolved and documented.
- Do not scrape vendor pages automatically into canonical facts. An agent must
  review changes because vendor documentation can regress or contradict the
  locked driver.
- New feature work updates capability status and opportunity status in the same
  change when it produces new evidence.
- Never commit secrets or irreversible security decisions to a generic example.

## Validation

### Knowledge system

- Schema validation rejects incomplete or contradictory entries.
- A repository test verifies that `AGENTS.md` and `CLAUDE.md` route both agents
  to the canonical registry.
- Initial entries cite the exact Waveshare schematic/repository, locked BSP
  source, physical findings, and relevant Espressif documentation.
- A deliberate discrepancy fixture proves that vendor claims cannot silently
  override a physical finding.

### Studio

- Every preview and screenshot is exactly 480 x 480 pixels.
- Approved static states pass simulator tests and the existing physical AMOLED
  gate before animation work.
- Studio reads device facts from the registry rather than a duplicate config.
- Preview, build, and install remain independently invokable.

### OTA

- Partition checks prove that both current and maximum permitted images fit in
  either OTA slot with the documented margin.
- Unit and integration tests reject truncated, corrupt, oversized, wrong-chip,
  wrong-project, and unauthorized images.
- Hardware tests cover successful update, interrupted upload, power loss before
  slot selection, crash before validation, automatic rollback, hotspot install,
  home-Wi-Fi install, maintenance SoftAP, and USB recovery.
- A deliberately broken UI-start build must roll back automatically.
- A healthy build without Internet or tokenserver access must still validate.
- Studio never reports success until the expected build reconnects and identifies
  itself, or explicitly reports that the previous version rolled back.

## Delivery order

1. Implement and populate the capability registry, device inventory, source
   ledger, agent routing, and validation.
2. Implement the VibePulse Studio preview MVP and keep physical AMOLED review
   directly after the approved static screen.
3. Design and implement the OTA foundation as its own safety-critical plan,
   then perform the one-time USB migration and failure-injection tests.
4. Add device discovery, diagnostics, and `Install wirelessly` to Studio.
5. Consider audio, BLE provisioning, battery operation, RTC wake, microSD, and
   advanced USB features individually through the opportunity radar.

Each numbered item gets its own implementation plan and verification gate. The
knowledge system is first because every later decision depends on distinguishing
what the device could do from what this Torget unit can safely do now.

## Primary references

- Waveshare board documentation:
  https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16
- Waveshare first-party repository, schematic, examples, and recovery firmware:
  https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-2.16
- Waveshare board BSP in Espressif Component Registry:
  https://components.espressif.com/components/waveshare/esp32_s3_touch_amoled_2_16
- ESP32-S3 datasheet:
  https://www.espressif.com/documentation/esp32-s3_datasheet_en.pdf
- ESP-IDF OTA and rollback:
  https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/system/ota.html
- ESP-IDF product security:
  https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/security/security.html
- ESP-IDF external RAM:
  https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-guides/external-ram.html
- ESP-IDF USB device stack:
  https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/peripherals/usb_device.html
- ESP-IDF USB host guidance:
  https://docs.espressif.com/projects/esp-usb/en/latest/esp32s3/usb_host.html
- ESP-IDF Wi-Fi provisioning:
  https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/provisioning/wifi_provisioning.html
