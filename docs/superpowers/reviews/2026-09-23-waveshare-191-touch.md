# Waveshare 1.91 Touch AMOLED — installation evidence

Date: 2026-09-23. Unit: `vibepulse-191-touch-01`. PCB revision unknown.
Firmware: `v1.1.0-16-g6b05e53-dirty`, ESP-IDF 5.5.2, built September 23 at
19:34:25. Source is the 1.91 port plus bounded FT3168 retry handling.
App SHA256: `1906f4317c0c6f13d5d6b2e1391187c7d40961bea7dc29983e85037d773135f0`.

## Observed passes

- USB identified ESP32-S3 rev 0.2, 16 MB flash, 8 MB PSRAM.
- Complete factory flash was read and digest-verified before writing; backup private.
- Native diagnostic was installed and segment hashes verified.
- Owner confirmed four correctly mapped touches: `1:1 2:1 3:1 4:1`.
- Phone reached the setup portal, submitted credentials and obtained an IP.
- After fixing the late-success bug, serial reported one successful NVS save;
  owner said the connection now worked. Setup closed and local usage fetch succeeded.
- Final firmware write verified. It automatically rejoined the saved network.
- A subsequent controlled USB reset rejoined the saved network at 7.5 seconds.
  The panic counter stayed at six across boots 17 and 18: no new panic in these
  two sampled boots. Earlier development builds had touch-initialization panics.
- The first transient touch NACK in both final boot samples recovered with one
  retry in 7 ms. This is measured recovery, not proof that all bus faults are gone.
- Five owner-supplied photographs show the real panel during boot and on the
  Codex weekly page. The 9% quota, reset time, header and footer appear readable
  within the glass on these static views. See `docs/img/191-touch/glass-*.jpg`.

## Defects found and corrected

The original quota font lacked percent and missing-data glyphs. A native 84 px
font adds them. Tracker summary spacing overlapped its pager; native raster
checks now enforce a clear strip. The QR screen omitted the second browser step;
it now explicitly says to join the setup network and open http://192.168.4.1.

The radio reported NO_AP_FOUND (201), then retried successfully, but setup only
accepted DHCP while its status remained CONNECTING. The stale error prevented
saving valid credentials. The guard now accepts fresh IP for a started trial
following transient failures, while rejecting old IP, failed/abandoned trials
and duplicate saves. A captured-sequence C regression and wiring tests pass.

The FT3168 occasionally NACKed both reads and initialization. A failed initial
mode write previously aborted startup. Board-specific bounded retries now handle
that response. Disabling monitor mode and lowering I2C to 100 kHz alone did not
solve the observed NACKs and must not be described as proven fixes.

Opening this board's USB serial port can reset it. Reopening the logger during
phone provisioning contributed to disruption. Use one persistent monitor and
inspect its saved output; distinguish explicit USB resets from actual panics.

## Scope and limits

Selected native 536 × 240 LVGL captures were reviewed. The full 154-frame matrix
was checked for dimensions, plus tracker clearance and QR raster checks. The host
suite passed on the initial port; targeted Wi-Fi regressions, onboarding/wiring
checks and firmware build passed after the connection fix.

The photographs are static and were taken under strong red room lighting, so they
do not establish calibrated colors or an exhaustive physical visual acceptance.
BOOT menu operation needs a separate final owner check. Motion/stress testing,
OTA, battery, SD, audio and IMU are not verified. On-glass approvals and denials
are disabled on this profile until the 90 px touch-target rule and a physical
round trip can be satisfied. This is a
USB-installed development port with local usage display, not a tagged release.
Other boards' firmware was not reflashed. Private credentials, compiled firmware,
raw logs and full flash images are excluded from publishable material.
