# Hardware opportunities

This radar identifies possible product directions; it does not restate the
hardware registry. Status is descriptive only, not permission. Validate every
required capability and prerequisite against the canonical hardware files
before planning work.

## Opportunity radar

| ID | Status | Required capabilities | Physical prerequisite |
| --- | --- | --- | --- |
| local-ota | designed | Wi-Fi, OTA A/B | one final USB bootstrap |
| completion-audio | candidate | ES8311, speaker output | confirm attached speaker and safe volume |
| ble-provisioning | candidate | BLE, Wi-Fi | radio/memory measurement |
| motion-gestures | candidate | QMI8658 | enclosure false-positive test |
| battery-mode | idea | AXP2101, battery connector | cell/polarity/current/thermal verification |
| rtc-wake | idea | PCF85063ATL, PMU | verify backup supply and wake path |
| microsd-history | idea | one-bit SDMMC | insertion/removal/write-interruption test |
| native-usb-modes | idea | USB device | reconcile Buddy and USB debug ownership |
| voice-controls | idea | microphones, speaker, network | privacy UI and full-duplex audio test |

## Decision context

| ID | User value | Relevant conflicts | Privacy implications | Why not authorized |
| --- | --- | --- | --- | --- |
| local-ota | Update a deployed screen without routine cabling. | Partition, recovery, and rollout policy must agree. | Network update checks expose device timing and endpoint metadata. | Requires a release, rollback, and bootstrap decision. |
| completion-audio | Make completed actions noticeable away from the display. | Playback can contend with other audio ownership and quiet-hours behavior. | Audible output may disclose activity to nearby people. | Requires speaker confirmation, volume limits, and product approval. |
| ble-provisioning | Set up Wi-Fi without rebuilding secrets into firmware. | BLE memory and radio use can compete with Wi-Fi workloads. | Provisioning handles network identity and credentials. | Requires measured coexistence and a credential-handling design. |
| motion-gestures | Offer hands-free shortcuts and orientation-aware actions. | Movement and auto-rotation can interpret the same sensor events differently. | Motion should remain local unless a separately approved design says otherwise. | Requires physical false-positive evidence and gesture UX approval. |
| battery-mode | Keep the display useful during brief power interruptions. | Power budget, charging, enclosure space, and thermal limits interact. | Battery telemetry should not become remote presence tracking. | Requires electrical and thermal verification plus an enclosure decision. |
| rtc-wake | Wake predictably while minimizing idle power. | PMU sleep state, wake ownership, and time recovery must align. | Schedules can reveal household routines if transmitted or logged. | Requires backup-supply and wake-path proof before design work. |
| microsd-history | Retain longer local history and export it offline. | Removal and interrupted writes can corrupt storage or block UI work. | Stored history needs retention, deletion, and physical-access rules. | Requires media lifecycle and failure-behavior decisions. |
| native-usb-modes | Enable direct diagnostics or device-mode integrations. | Buddy and USB debugging may claim the same interface and lifecycle. | A cable could expose logs, identifiers, or locally stored data. | Requires an ownership, access-control, and recovery decision. |
| voice-controls | Allow natural hands-free control and spoken feedback. | Capture, playback, network latency, and full-duplex audio compete for resources. | Microphone audio and network transmission require visible consent, mute, retention, and deletion rules. | Requires privacy approval, physical audio tests, and a service architecture. |

None of these ideas authorizes feature work, flashing, fuse changes, or
hardware modification.
