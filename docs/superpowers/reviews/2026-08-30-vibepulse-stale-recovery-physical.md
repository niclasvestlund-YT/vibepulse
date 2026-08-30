# VibePulse stale-recovery physical review — 2026-08-30

## Outcome

**FIRST REMEDIATION FAILED; HARD-RECOVERY DATA PATH AND CANONICAL APPROVE NOW
PASS BEYOND THE RECOVERY WINDOW. DEDICATED-POWER AND LITERAL-STALE GATES REMAIN
NOT TESTED FOR THE FINAL IMAGE.**
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

A second candidate adds bounded escalation to the first watchdog. At 60
seconds without a real quota success it recycles Wi-Fi and wakes the quota
task, which waits for a new IP before retrying; after another 45 seconds
without success it performs one controlled restart. A reboot is disarmed until
a new real success. Host policy tests, wiring tests, and full ESP32-S3 CI pass.

The final merged image `v1.0.0-30-g000ebe4` was then built from a clean
checkout, matched the live service's `otaAvailableVersion`, and was written to
the identified ESP32-S3. Esptool hash-verified bootloader, partition table,
OTA initial data, and app; NVS was outside every write range. On computer USB,
serial evidence reported a successful token fetch at approximately 415 seconds
of uptime—well beyond the 105-second recycle-plus-restart window—with stable
heap. The exact `Ser du APPROVE?` question also passed with a visible human
`Ja` and returned `answered`, option index 0. A later literal-STALE question
timed out to computer fallback, so silence was discarded and the final image's
literal `STALE` absence is still **NOT TESTED**. The final image was not moved
to dedicated 5 V during this checkpoint; sustained dedicated-power acceptance
also remains **NOT TESTED**.

The macOS LaunchAgent now runs the durable checkout at merge `000ebe4`; doctor
accepted the live source fingerprint, discovery was ready, the Claude
credential was ready, and Claude week, Fable/model week, and Codex week stale
flags were all false. The first atomic replacement encountered a transient
`launchctl bootstrap` failure and automatically restored the old service; an
identical retry succeeded. PR #60 adds a bounded retry for that observed race
while retaining fail-closed rollback. Its complete duplicated CI matrix passed.
The new startup's historical log scan took 211 seconds, during which the light
health endpoint remained responsive but `/api/tokens` requests timed out. That
startup-latency boundary is operational evidence, not a panel PASS.

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
| Final hard-recovery flash | PASS | Clean `v1.0.0-30-g000ebe4` matched the service advertisement; full ESP32-S3 build had 61% app-partition headroom; all four written images were hash-verified and NVS was untouched |

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
| Host runtime provenance | PASS | Durable checkout, live service revision, source fingerprint, and advertised firmware version matched `000ebe4` / `v1.0.0-30-g000ebe4` |
| Physical APPROVE | PASS | Exact question `Ser du APPROVE?`; visible-state instruction required APPROVE; human tapped `Ja`; returned `answered`, option index 0, answer `Ja` |
| Initial literal `STALE` absence | PASS, transient | Computer fallback was discarded; the user then directly inspected the main view and confirmed that `STALE` was gone |
| Repeated physical interaction | **FAIL** | The follow-up panel request timed out; silence and computer fallback were not counted as approval |
| Sustained literal `STALE` absence | **FAIL** | The user later confirmed that `STALE` had returned |
| New watchdog diagnostic runtime | PASS, bounded | Quota HTTP completed repeatedly beyond the former failure point, through approximately 6 minutes 19 seconds, without a heap collapse |
| Disconnect-only watchdog dedicated-power runtime | **FAIL** | The relay-enabled image later became stale again while local and relay data stayed fresh and the board remained reachable |
| Hard-recovery candidate build | PASS, host only | Staged recycle/wake/restart policy and target wiring pass host tests; the ESP32-S3 image builds successfully |
| Final hard-recovery data runtime | PASS, bounded on computer USB | Serial reported a successful token fetch at about 415 seconds, beyond the 105-second hard-recovery window, with stable heap |
| Final hard-recovery dedicated-power runtime | **NOT TESTED** | The final image was not moved to dedicated 5 V during this checkpoint; computer USB is not accepted as the operating-power gate |
| Final canonical physical APPROVE | PASS | Exact `Ser du APPROVE?`; visible human `Ja`; returned `answered`, option index 0, answer `Ja` |
| Final literal `STALE` absence | **NOT TESTED** | The follow-up question timed out to computer fallback; silence was not treated as proof of the glass state |
| Final direct-LAN polling | WAIT | No direct panel poll was confirmed after the service restart; the successful data and question evidence used the relay-capable firmware paths |
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
9. A valid LaunchAgent can hit a short post-`bootout` bootstrap race. Retry
   within a fixed small bound, then restore the prior service; never loop.
10. Near-zero host disk space can break atomic state writes and greatly extend
    startup work. Clean only regenerable build output after recording the
    flashed hashes, and keep light health separate from the heavy quota scan.

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

## Second candidate — bounded physical data and APPROVE pass

The replacement policy intervenes before the 120-second glass freshness
boundary: recycle at 60 seconds, wake the quota task, retry after a new IP,
then one controlled device restart after a further 45 seconds without a real
success. A successful
quota response clears the incident. Cold boot, LAN-only, disassociated, and
clock-regression states remain fail-closed, and after a restart the guard does
not arm again until a new success, so a persistent upstream outage cannot form
a reboot loop.

The host policy and wiring tests pass, the firmware builds, the final merged
image was hash-verified on the target, a real token fetch succeeded beyond the
recovery window, and the canonical physical question returned the human's
`Ja`. Full acceptance still requires the same dedicated-power stale-window
test plus literal absence of `STALE`; direct polling must be reported
separately rather than inferred from healthy relay traffic.
