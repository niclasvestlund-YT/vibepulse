# Unverified opportunities for Waveshare 1.8 V2

The initial VibePulse port targets the quota and agent-status display, touch,
Wi-Fi and the BOOT settings control. Other board components are separate
follow-up work and are not prerequisites for the display port.

- Audio input/output, RTC, battery/PMU telemetry, IMU rotation and microSD need
  separate driver, pin/resource, privacy and physical validation.
- The 1.8 V2 BSP and touch-controller naming need to be checked against the
  actual CST820 device and the exact resolved component source.
- OTA must identify the hardware model and reject firmware for a different
  display before this board uses the shared update mechanism.
- The portrait 368 × 448 screen needs native-size review for every app and
  system surface; simulator output must not be presented as physical evidence.

See the [working port journal](../../../docs/porting-journal-waveshare-amoled-18-v2.md)
for the current engineering record.
