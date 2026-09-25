# Physical review: Waveshare ESP32-S3-Touch-AMOLED-1.8 V2

**Date:** 2026-09-25

**Unit:** `vibepulse-amoled-18-v2-01`, owner-confirmed V2 label

**Firmware:** VibePulse ESP-IDF 5.5.2, `waveshare_18_v2`; app SHA-256
`e383a4f5783dad3bd98bb3ed89132cf82ea109a4eacaf5d6eb66ce42614f96b6`

**Flash:** esptool `--verify` reported `Hash of data verified` for bootloader,
partition table, OTA data and application. NVS was not erased.

## Observed and owner-reported

- The owner supplied two photographs that show VibePulse on the physical 368 ×
  448 portrait display after the USB flash. They are not included in this public
  repository because the live screen contains account quota readings. The
  public simulator preview uses fixture data and is labeled separately.
- The owner reports that the display, touch, Wi-Fi/data path and BOOT settings
  all work on this unit. This is owner-reported physical verification; the
  photos independently show only the live display, not the touch gesture or
  network exchange.
- The 1.8 V2 profile therefore passes the owner's display/touch/network and
  settings acceptance for this named unit. This does not validate other units
  or V1 boards.

## Explicitly outside this review

- Audio, microphone, speaker, RTC, IMU, battery/PMU, microSD, OTA, Windows,
  long-running Wi-Fi/TLS stress, and transform-layer runtime high-water.
- The owner reports that the iPhone QR provisioning flow is still cumbersome.
  Network operation was checked, but onboarding usability is not accepted;
  improving it is follow-up product work.
- No full flash backup was taken for this development unit, at the owner's
  explicit direction that it held only standard data. This is not a default
  procedure for future installs. No personal serial identifier or network
  details are included here.

## Public supporting image

- `docs/img/18-v2/vibepulse-18-v2-simulator.png` — native-size fixture preview,
  not physical evidence.

No owner photo, flash log, credential, network detail or unique serial
identifier is published in this review.
