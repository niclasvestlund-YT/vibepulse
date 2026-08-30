# VibePulse stale-recovery physical review — 2026-08-30

## Outcome

**FIRST REMEDIATION FAILED SUSTAINED DEDICATED-POWER ACCEPTANCE; HARD-RECOVERY
CANDIDATE BUILT BUT NOT FLASHED.**
The physical unit `torget-home-01` now runs
`v1.0.0-25-g054db68`, built from the stale-recovery branch for PR #58. The
previous `v1.0.0-18-g3a131a2` checkpoint was byte-identical to merged `main`
`f672a14` and contained the stale-glass diagnostic and runbook changes from
PR #57.

The firmware flash, initial wall-powered network recovery, local service
discovery, fresh Claude/Fable/Codex payloads, touch input, and one canonical
physical local-LAN APPROVE round trip passed. The user also confirmed that the literal
`STALE` label disappeared immediately after recovery. That was not durable:
after several minutes the panel became `STALE` again. At the failure
checkpoint the host and the ESP32-compatible numbers relay still served fresh
data, the ESP32 still answered ICMP, but direct application polling was more
than five minutes old and a new panel interaction timed out. The evidence
therefore proves a live network interface with stalled device-side HTTP work;
it does not prove a reliable stale recovery.

The new watchdog image was then written with NVS preserved and booted with the
expected version. Under bounded serial observation its quota fetch succeeded
repeatedly through approximately 6 minutes 19 seconds of uptime, including
successes after the old roughly 5-minute failure point, with stable internal
heap. That is a physical PASS for the candidate's computer-USB diagnostic
window, but not yet the dedicated-power acceptance gate. The immediate
canonical interaction still timed out. Simultaneous DNS-SD browsing found two
different VibePulse hosts on the LAN; the flashed image had the encrypted
interaction relay disabled, so a healthy first DNS-SD result could bind the
direct question path to the wrong computer. Numbers recovery and question
delivery are therefore recorded as two distinct issues.

The follow-up app-only flash preserved NVS and installed the same watchdog
candidate with the encrypted interaction relay enabled. The device booted the
expected version, joined Wi-Fi, fetched fresh data, and logged that the
encrypted interaction relay had started. The immediate canonical question
then passed: the panel visibly showed APPROVE, a human tapped `Ja`, and the
tool returned `answered`, option index 0, answer `Ja`. This closes the observed
multi-host question-routing failure for the relay-enabled candidate. It did
not close the separate sustained dedicated-5-V stale-recovery gate: the panel
became stale again, and two later interaction checks ended in computer
fallback and timeout rather than a panel answer.

A second candidate now adds bounded escalation to the first watchdog. At 60
seconds without a real quota success it recycles Wi-Fi and wakes the quota
task, which waits for a new IP before retrying; after another 45 seconds
without success it performs one controlled restart. A reboot is disarmed until
a new real success. Host policy
tests, wiring tests, and an ESP32-S3 build pass, but this image has not been
flashed. It remains **NOT TESTED** on the physical stale-window gate.

The live macOS tokenserver also reports an older source revision because its
launchd job still points at a historical validation worktree. Its active Claude
probe, saved credential, and all served stale flags are healthy, so that
provenance drift does not explain this glass-only stale incident. It is still a
host-hygiene warning: release validation must restart the service from the
intended merged checkout rather than silently accepting an old process.

No credential, account identifier, quota value, private address, relay route,
device key, or private URL is recorded here.

## Flash evidence

| Gate | Result | Sanitized evidence |
|---|---|---|
| Explicit authorization | PASS | The user explicitly said to flash the connected ESP32 |
| Target identity | PASS | The resolved USB target identified as ESP32-S3 with 8 MB embedded PSRAM and USB Serial/JTAG |
| Build identity | PASS | App descriptor and image strings reported `v1.0.0-18-g3a131a2`; discovery strings were present |
| Main-tree equivalence | PASS | The build commit tree and merged `main` tree had the same Git tree object |
| Safe write scope | PASS | Bootloader, partition table, OTA initial data, and app were written; NVS was not included |
| Image verification | PASS | Esptool verified the hash of every written image and exited successfully |
| Recovery behavior | PASS | Automatic reset could not enter the ROM loader; the documented BOOT + RESET sequence did, before any write occurred |
| Watchdog image flash | PASS | `v1.0.0-24-ga16512a` booted after a hash-verified write of bootloader, partition table, OTA initial data, and app; NVS remained outside the write ranges |
| Relay-enabled app flash | PASS | `v1.0.0-25-g054db68` booted after a hash-verified app-only write; NVS, partition table, and other data partitions remained outside the write range |

## Runtime evidence

| Gate | Result | Sanitized evidence |
|---|---|---|
| Computer-USB runtime | FAIL as operating mode | No direct panel poll appeared inside the bounded 90-second window |
| Dedicated-power startup | PASS | After moving to a dedicated 5 V supply, the panel completed a signed local-LAN interaction and later resumed direct LAN polling |
| Sustained dedicated-power runtime | **FAIL** | After several minutes the panel became stale again although the ESP32 still answered ICMP and both host and relay payloads remained fresh |
| Service discovery | PASS | Host discovery reported `ready` |
| Initial direct panel contact | PASS | Root health changed from `waiting` to `ready` after two confirmed panel polls |
| Sustained direct panel contact | **FAIL** | The last confirmed `/api/agent-status` poll aged past five minutes and was not renewed |
| Provider freshness | PASS | Claude weekly, Fable/model-week, and Codex weekly stale flags were all false |
| Host runtime provenance | WARN | The live launchd service runs an older validation-worktree revision; current data is fresh, but current-main validation remains pending |
| Physical APPROVE | PASS | Exact question `Ser du APPROVE?`; visible-state instruction required APPROVE; human tapped `Ja`; returned `answered`, option index 0, answer `Ja` |
| Initial literal `STALE` absence | PASS, transient | Computer fallback was discarded; the user then directly inspected the main view and confirmed that `STALE` was gone |
| Repeated physical interaction | **FAIL** | The follow-up panel request timed out; silence and computer fallback were not counted as approval |
| Sustained literal `STALE` absence | **FAIL** | The user later confirmed that `STALE` had returned |
| New watchdog diagnostic runtime | PASS, bounded | Quota HTTP completed repeatedly beyond the former failure point, through approximately 6 minutes 19 seconds, without a heap collapse |
| Disconnect-only watchdog dedicated-power runtime | **FAIL** | The relay-enabled image later became stale again while local and relay data stayed fresh and the board remained reachable |
| Hard-recovery candidate build | PASS, host only | Staged recycle/wake/restart policy and target wiring pass host tests; the ESP32-S3 image builds successfully |
| Hard-recovery candidate physical runtime | **NOT TESTED** | The second candidate has not been flashed; no glass result may be inferred from its build |
| Relay-disabled multi-host question routing | **FAIL, superseded** | Two VibePulse DNS-SD services were present; the relay-disabled image could select a different healthy host, and the canonical question timed out |
| Relay-enabled multi-host question routing | **PASS, immediate** | The canonical question showed APPROVE and returned the human's `Ja` response as `answered`, option index 0 |

## Lessons locked in

1. Fresh host and relay data do not prove fresh glass; require panel-contact
   evidence or a human glass check.
2. A failed esptool connection before erase/write is recoverable and must not
   be described as a partial flash.
3. This unit needs the silent BOOT button held across RESET to enter the ROM
   loader when automatic reset fails.
4. Computer USB is suitable for download mode but is not a valid runtime-power
   acceptance setup for AMOLED plus Wi-Fi.
5. Interaction success and a fresh data payload are separate gates from the
   literal stale-label visual check.
6. One successful round trip after boot is not a sustained-runtime pass. A
   release gate must cover at least the panel's stale window plus recovery
   margin and must include a second interaction.
7. ICMP reachability does not prove that application HTTP tasks are making
   progress. Record both network-interface liveness and last successful panel
   request.
8. Fresh numbers do not prove correct question routing. On a LAN with several
   VibePulse hosts, first-result DNS-SD selection is not user intent; use the
   encrypted interaction relay for a shared panel.

## First candidate — failed sustained acceptance

The follow-up firmware change disables ESP-IDF's default modem sleep for this
wall-powered live display, closes the encrypted relay client on every failure
exit, and adds a VibePulse-specific recovery policy. The policy arms only
after a real quota success, only while Wi-Fi still reports association, and
only when an independent numbers relay is configured. Its single Wi-Fi recycle
had no escalation when application HTTP still made no progress.

The HTTP part passed only a bounded computer-USB diagnostic window and then
**FAILED** sustained dedicated-power operation. The relay-enabled image passed
one immediate canonical physical question on the multi-host LAN, but later
checks did not reach answer buttons. Direct LAN discovery alone still cannot
prove which host owns a question on this LAN; the encrypted interaction relay
remains required for the shared-panel setup.

## Second candidate — host verified, physical gate pending

The replacement policy intervenes before the 120-second glass freshness
boundary: recycle at 60 seconds, wake the quota task, retry after a new IP,
then one controlled device restart after a further 45 seconds without a real
success. A successful
quota response clears the incident. Cold boot, LAN-only, disassociated, and
clock-regression states remain fail-closed, and after a restart the guard does
not arm again until a new success, so a persistent upstream outage cannot form
a reboot loop.

The host policy and wiring tests pass and the firmware builds. No physical
claim is made yet. Full acceptance requires explicit flash authorization, the
same dedicated-power stale-window test, recent direct polling, literal absence
of `STALE`, and a repeated canonical question returning the human's `Ja`.
