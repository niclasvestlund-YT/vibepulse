# Unverified opportunities for 2.41 V2

The accepted product scope is VibePulse's fixed landscape display with touch
and Wi-Fi. Other board features are not prerequisites and are not implicitly
authorized work.

- Automatic rotation needs V2-specific IMU wiring, calibration and paired
  panel/touch tests; the 2.16 constants are not evidence for V2.
- OTA needs a board-identification/mismatch gate and a dedicated recovery test
  before it becomes the recommended update path for multiple display models.
- Audio, battery, RTC, microSD, BLE and external expansion need their own source
  inventory, memory/pin budgets and physical tests before product claims.
- Interaction replies need explicit opt-in keys and a physical end-to-end
  answer test on this unit. Quota display and simulator APPROVE buttons alone
  do not establish that support.

Use [the next-display checklist](../../../docs/adding-a-display.md) when a new
model arrives instead of extending this unit's verification to it.
