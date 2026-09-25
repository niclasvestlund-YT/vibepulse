# Waveshare 1.8 V2 hardware profile

This registry applies only to the rear-label-confirmed V2 revision of the
Waveshare ESP32-S3-Touch-AMOLED-1.8. V1 uses different panel and touch silicon.
It is separate from the existing 2.16 and 2.41 registries; no physical
verification transfers between them.

| Resource | V2 information |
|---|---|
| Compute and storage | ESP32-S3R8, vendor-listed 16 MB flash and 8 MB PSRAM |
| Panel | CO5300 QSPI AMOLED, 368 × 448 portrait |
| Touch | CST820 over I2C; upstream BSP 2.0.3 selects a compatible touch family |
| Power and reset | Board support is delegated to Waveshare BSP 2.0.3; exact source behavior remains under review |
| Controls | BOOT and PWR controls are present; BOOT's runtime settings mapping still requires physical verification |
| Other components | AXP2101, PCF85063, QMI8658, ES8311, microphone, speaker and microSD are listed by Waveshare; not enabled or verified by this port |

The display driver and touch coordinates are treated as a pair. The 1.8-inch
revision is explicitly V2 because both driver chips changed under the same
retail model name. The named unit now runs the `waveshare_18_v2` profile;
owner-reported physical acceptance and remaining measurement limits are in
the [physical review](../../../docs/superpowers/reviews/2026-09-25-waveshare-amoled-18-v2-physical.md).
The transform-layer runtime high-water remains unmeasured. See the [porting
journal](../../../docs/porting-journal-waveshare-amoled-18-v2.md) for source
revisions, corrections and lessons for future ports.
