# Deferred capabilities

IMU rotation, battery, RTC, microSD, BLE and audio are not part of this port.
The 2.16 rotation calibration is not applicable. SD wiring depends on PCB
revision, which must be inspected before using it. Motion and long network/TLS
stress require separate measurements. OTA remains disabled; use USB and the
correct board profile. Physical interaction replies require an end-to-end test.
