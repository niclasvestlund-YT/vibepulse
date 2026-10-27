# Local preview validation, 2026-10-27

The owner explicitly authorized flashing after the original build-only request.
The identified unit is `grok-p4-7b-c-01`, SKU 32511, ESP32-P4 revision 3.2,
32 MB flash. The other connected display was excluded.

Before installation, the first 3 MiB were read and their digest verified against
the device. This rollback image covers every sector changed by this preview.
A full 32 MiB backup could not be completed; this is not a full-flash backup.
Private rollback images and serial logs are retained locally, not committed.

Bootloader, partition table and application writes passed esptool hash
verification. Subsequent display corrections write only the application.
The owner confirms rotation 180 gives an upright image. The owner reports
touch does not work; touch acceptance is **failed/pending investigation**.
GT911 detection at boot alone does not establish working touch input.

The P4 target builds using ESP-IDF 5.5.2 with the documented revision 3.2
watchdog workaround. The host Python suite passed 1,050 tests, with 13 skips.
The broader `./test/run.sh` gate was also attempted under Git Bash on Windows:
it stops in two existing OpenPulse tests that assert POSIX mode `0600` where
Windows reports `0666`. Neither those tests nor OpenPulse sources were changed.
The full shell gate is therefore not a PASS. Ruff and this board's hardware
registry validation pass.
Speech and webhook tests use local fakes. The native UI capture is generated
from the same LVGL component used by the target.

Installed diagnostic application SHA-256:
`cd42f4f1d25300e2d468a71436c68a3535e4229237accfdf72dff278f86b8323`.
Size: 2,090,736 bytes, installed at `0x20000`. It logs touch scans and coordinates
over USB. The checked-in `GROK_TOUCH_DIAGNOSTICS` option now makes those logs
opt-in (disabled by default). The default, non-diagnostic build also succeeds;
it has not replaced the diagnostic image while physical testing is pending.

No Wi-Fi or voice-service credentials were provisioned. Microphone capture,
speaker playback, live routine callbacks, latency and continuous-use stability
remain unverified. The preview is not a completed voice installation.
