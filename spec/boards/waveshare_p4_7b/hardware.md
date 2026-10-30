# Waveshare P4 7B-C development target

Exact board: ESP32-P4-WIFI6-Touch-LCD-7B-C, SKU 32511. Native 1024x600 MIPI display, GT911 touch, ES8311 output and ES7210 microphone codecs. P4 revision 3.2 and 32 MB flash were identified by a read-only chip query before this feature request. C6 provides Wi-Fi over SDIO; compatibility with the factory C6 image has not been tested.

The dedicated project is `projects/grok_bot/firmware`; existing S3 build profiles are unchanged. BSP 3.0.1 owns display/touch/I2C/I2S. The audio adapter follows the vendor two-channel example, averages microphone channels to 16 kHz mono PCM, and duplicates reply mono to the two speaker I2S slots. Microphone channel selection and acoustic performance require physical validation.

Display/touch transform: rotation 180, swap_xy=0, mirror_x=1, mirror_y=1, matching BSP 3.0.1 defaults. The owner confirms the image is upright, but touch acceptance fails and remains under investigation. I2C SDA7/SCL8. Audio MCLK13/SCLK12/LRCK10/DOUT9/DIN11/PA53. Do not reuse an S3 pin map.

The owner subsequently authorized a local preview installation on 2026-10-27. Flash hash verification and upright-image confirmation do not establish touch or audio acceptance. Wi-Fi, microphone, speaker, latency and memory high-water remain physically unverified. No camera or wake-word engine is enabled. See the project preview-validation record.
