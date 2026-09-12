# Flash session 2026-09 — KEY3 physical review — 2026-09-06

## Outcome

**DRAFT, FOR REVIEW. USB FLASH TO `v1.0.0-67-ge51b79f` PASSED AND THE `settings`
INTERNAL-RAM QUESTION IS SETTLED AT `+0 B`. §2 BOOT-LOG EVIDENCE IS COMPLETE. A
MEASUREMENT GAP WAS FOUND: THE INTERNAL-HEAP LOW-WATER IS A SUM OF PER-REGION
MINIMA THAT CANNOT ANSWER THE FLUSH QUESTION, AND NOTHING TRACKS THE DMA BLOCK
BETWEEN SAMPLES (OBS-37). NONE OF THE KEY3 MANUAL TESTS WERE RUN — §1, §2, §3 AND §4.1–4.3 ARE
ALL NOT EXERCISED. A SIX-HOUR UNATTENDED PASSIVE OBSERVATION COMPLETED WITH NO
ALARM; IT IS NOT THE RUN SHEET'S §5. §3.5 CODEX AND MANUAL-TEST 4.4/4.5 ARE NOT
EXERCISED.**

The evening's substance is the flash and the OBS-37 investigation, not the
checklist. Three deliberate OTA-window cycles were measured because the
investigation needed them; they are not manual-test 4.1/4.2 results, because
4.1–4.3 were never walked as written and their pass criteria (menu route,
countdown, `KEY3 CLOSES` footer, early close with no upload) were not checked.

The physical unit `torget-home-01` now runs `v1.0.0-67-ge51b79f`, flashed over
USB on 2026-09-06 at 01:10. The run sheet `docs/flash-session-2026-09.md` was
followed; three of its premises turned out to be wrong on this machine and are
corrected below.

## Deviations from the run sheet

1. **The inventory was wrong about what was on the glass.** The sheet and
   `spec/device-units.yaml` both said `v1.0.0-25-g054db68` (flashed
   2026-08-30). The boot banner said `v1.0.0-33-g51e8d0e-dirty`, compile time
   `Sep 2 2026 23:37:55` — eight commits further along, built three days after
   the recorded flash, and **built from a dirty tree**. The exact source of the
   running image was therefore not reconstructible. The sheet's behavioural
   premise still held: #72, #73, #76 and #77 are all absent from `51e8d0e`, so
   the image had no SETTINGS menu, no overlay cost lines and no QR fix.

2. **The OTA delivery path in §1 could not work.** The running image logged
   `ota-service: inget OTA-token i secrets.h — uppladdning avstängd`, i.e. the
   `#else` branch of `components/torget_ota/ota_service.c:549`: `TG_OTA_TOKEN`
   was undefined at compile time. `s_token_usable` gates only the upload
   endpoint's authorization check (`ota_service.c:195`), so the maintenance
   window would still have opened and drawn its ring while every upload was
   rejected. Delivery over the air was impossible; the flash went over USB with
   explicit user authorization. This checkout's `secrets.h` has a valid token
   (64 lowercase hex, verified without printing it), so the flashed image has
   OTA receiving enabled again and the rest of the session can go over the air.

3. **`python3` in the sheet is the wrong interpreter here.** Bare `python3` is
   the system 3.9.6, which has no `tomllib`, so `tools/vibepulse_setup.py
   doctor` crashes on import. The service runs `.venv/bin/python` (3.12.13);
   that is what doctor and smoke were run with.

Two smaller notes: `idf.py flash monitor` cannot be run from a non-TTY context
(`Monitor requires standard input to be attached to TTY`) — the monitor must be
started in its own terminal, as §0.5 already says. And attaching the serial
monitor to a *running* panel reboots it (`rst:0x15 USB_UART_CHIP_RESET`,
firmware reports `omstartsorsak USB-reset (11)`). `docs/lessons.md` (2026-08-13)
listed serial-monitoring a running board as unverified; it is now verified, with
the answer "works, but costs a reboot".

## Companion provenance

Buddy was excluded, as the build's own line confirms:
`Torget: Vibbe/Buddy är bortvald (TORGET_WITH_BUDDY=OFF, frysläxan 2026-08-14)`.
`~/Buddy` is at `b7002f0`, clean, and was not compiled in.

Solelkollen **was** compiled in, and cannot be attested the way the sheet asks:

```
Companion: Solelkollen
  Path:          ~/Solelkollen/components/app_solelkollen
  git describe:  UNAVAILABLE — the directory is not a git checkout, and there
                 is no .git anywhere in the chain up to /
  Contents:      8 files, 48 KB, modified 2026-08-12..13
  sha256:        0b14f6bfea1390b7b7550384424d656aef2e6a4ec27e249a1ba16d75d6418869
                 (method not recorded by the session that computed it — see
                 the source record's note; a fingerprint, not yet reproducible)
  In spec/hardware-sources.yaml: yes, since this PR
```

The user authorized the flash with this component included and unversioned,
recorded here by path and content hash, and in `spec/hardware-sources.yaml` as
the `torget-main-e51b79f-flash-2026-09-06` source's companion revision.

## §2 — boot log

Banner, on the flashed image:

```
I (986)  app_init: App version:  v1.0.0-67-ge51b79f
I (991)  app_init: Compile time: Sep  6 2026 00:39:48
I (1100) torget: boot: torget v1.0.0-67-ge51b79f (byggd Sep  6 2026 00:39:48,
         IDF v5.5.2), omstartsorsak USB-reset (11)
```

No `-dirty`. Identical to the checkout, the build and the binary.

The three overlay cost lines, logged once each at boot:

```
overlaykostnad wifi-setup: LVGL-pool +7572 B (pool 79100/251948 använt), internt +0 B (kvar 143275)
overlaykostnad settings:   LVGL-pool +3520 B (pool 82620/251516 använt), internt +0 B (kvar 143275)
overlaykostnad ota:        LVGL-pool +2504 B (pool 85124/251204 använt), internt +0 B (kvar 143275)
```

**`settings` costs `+0 B` of internal RAM.** That is the figure the AMOLED rule
asked for, and it retires the internal-RAM worry with evidence: the FEATURES row
has no internal budget to fit under. All three overlays live entirely in the
LVGL pool, which is 34 % used with all three accounted for.

## §2 — the heap drop: the measured old/new comparison, and a first reading later discarded

Steady-state internal heap is roughly half what the previous image showed. The
comparison below is measured; the explanation that followed it that night (the
relays' TLS churn) was the initial reading and is marked discarded further
down — OBS-37 identified no trigger for the drop.

| | old `v1.0.0-33` (55 min up) | new `v1.0.0-67` (95 s up) |
|---|---|---|
| internal free, stable | ~117 000 | ~55 000 |
| largest DMA block | 40 960 | 19 456–23 552 |
| lowest ever | 76 435 | 19 167 |

It is not a leak: the figure is flat from t=33 s onward and recovers to 59 027 by
t=95 s. Comparing the relay and transport lines in the two boot logs is what
the initial reading rested on — the reading marked discarded below, since
OBS-37 found the TLS correlation unsupported and identified no trigger. The
comparison itself stands:

| | old `v1.0.0-33` | new `v1.0.0-67` |
|---|---|---|
| `esp-x509-crt-bundle: Certificate validated` | **0** in 55 min | **412** in 17 min (one per 2.5 s) |
| `interaction-relay: krypterad reläkanal startad` | absent | present, t=9.4 s |
| `tokens: hämtning` | `stale claude=1`, 0.00 Mtok, 0 sessions | `stale claude=0`, 7.54 Mtok, 1 session |
| `tokens: max tracker` | `stale=1`, streak 0 days | `stale=0`, streak 28 days |

**Initial reading, later discarded — see OBS-37.** The old image did no TLS at
all: zero handshakes across its whole uptime, no encrypted interaction relay,
and every payload marked stale. Its roomy heap was the heap of a panel that was
not doing its job. The new image runs a continuous stream of TLS sessions — the
encrypted interaction relay, the numbers relay, the tokenserver fetches that now
succeed, and Solelkollen's HTTPS API — and mbedTLS session buffers are
internal/DMA-capable, which was the first explanation offered for the ~60 kB
difference. The interval analysis in OBS-37 later found the TLS correlation
unsupported and identified no trigger for any low-water step, so this stays
here as the discarded first hypothesis; what survives is the measured old/new
comparison above.

The one part that held: the drop is not a cost of the new overlays, which is
consistent with all three reporting `internt +0 B`. The number to watch in §5
is therefore the largest DMA block under repeated SETTINGS opens, which by the
overlay accounting should not move at all.

One item for the backlog falls out of this: **412 handshakes in 17 minutes, one
every 2.5 seconds**, suggests TLS connections are not being reused across polls.
That is heap churn and radio time for no obvious benefit.

## Observability findings

- `W torget-http: oväntad statuskod 502 (https://solelkollen.se/api/glance)`,
  followed by `solelkollen: hämtningen avvisad, värden står kvar`. The companion
  fails soft against an upstream 502. Seen repeatedly (t=11.5 s, t=43.1 s).
- `E QMI8658: Failed to read WHO_AM_I register`, immediately followed by
  `QMI8658 initialized successfully`. Present on **both** images, so not a
  regression from this flash.
- TLS handshake rate, as above.

## Not exercised

- **§3.5 Codex smoke test** — not run tonight, by user decision. `doctor`
  reports four blocking FIX items: the `vibepulse@torget` plugin is not
  installed, the MCP bridge is not registered with its 130 s timeout,
  `approval_policy=never` would silently hide permission cards (the #82
  finding), and hooks trust is not machine-readable. A real APPROVE tap
  returning `answered`/`option_index: 0` was therefore not obtainable.
- **manual-test 4.4** — needs a second pushed build with green CI; not attempted.
- **manual-test 4.5** — the post-delivery re-arm belongs to the OTA delivery
  path, which was not used; the image arrived over USB.

## Host state

Tokenserver restarted from this checkout before the session
(`rev e51b79f`); `doctor` reports `Tokenserver source: live fingerprint matches
this checkout`; `tools/tokenserver/smoke.py` 10 ok / 0 warnings / 0 errors. The
panel's LAN polling is confirmed live in the serial log.

The Mac's disk was **100 % full** (117 MiB free) at preflight and had to be
cleared before anything could be built; a full disk had already broken one tool
invocation with `ENOSPC`. Package caches and the stale 2026-08-30 build tree
were removed with user approval. Removing that tree also permanently retired the
stale-binary hazard the sheet warns about in §1 — it is no longer merely outside
the `build*` glob, it is gone.

## FINDING: the heap low-water cannot answer the flush question

Discovered from the serial log before the §1 gesture tests had produced a
single result. The firmware carries its own guard for this and it had fired on
76 of roughly 120 samples since 23 seconds after boot (43 % over the full
session):

```
W (307120) torget: LÅGT DMA-block: 19456 byte (flush behöver 11520) — nära fryströskeln
E (316778) esp_lvgl:adapter: esp_lv_adapter_lock(751): Failed to acquire LVGL lock
```

Counts over ~20 minutes of uptime on `v1.0.0-67-ge51b79f`:

| Signal | Count |
|---|---|
| `LÅGT DMA-block … nära fryströskeln` | **76** of roughly 120 samples, about 63 %, since t=23 s (the full session came to 1 083 of 2 527, 43 %; see OBS-37) |
| `esp_lv_adapter_lock: Failed to acquire LVGL lock` | **10**, clustered at t≈317–335 s, t≈819–827 s, t=1188 s |

The number this finding rests on is `lägsta någonsin`, which the periodic `heap:` line
tracks separately from the sampled value. Precisely: it is
`heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)` (`main/main.c:650`),
which ESP-IDF computes by summing each internal heap region's own lifetime
minimum. It is neither a DMA-block size nor a snapshot of one instant — the
regions' minima can come from different moments. The only block figure on that
line is the sampled `DMA största`. The first draft of this finding called it a
DMA-block low-water; the second called it the total free at one instant; it is
neither.

```
t=13 s   lägsta 44199
t=33 s   lägsta 19167
t=340 s  lägsta 18991
t=605 s  lägsta 18451
t=843 s  lägsta 11191
t=935 s  lägsta 11143     <-- flush needs 11520
```

**11 143 < 11 520.** Around t=843–935 s the summed low-water reached 11 143 B.
Because it is a sum of per-region minima taken at possibly different moments,
it does not establish an instant at which an 11 520 B allocation was
impossible, and it says nothing about the DMA block; it establishes only that
the regions were squeezed harder than the sampled line ever showed. Whether a
flush allocation ever fails is not measured. The panel did not
freeze — it is still rendering, and the sampled block recovers to
19 456–23 552 — but the margin against the freeze this repo has history with
may have been gone at some instant, and that was before any manual test ran. (The
`LÅGT DMA-block` guard itself fires below twice the flush size, 23 040 B, so
the 19 456 B it reports is not a block that is too small.)

The 10 s `heap:` sampling never observed a block below 19 456 in this period.
`lägsta någonsin` advanced between those log lines, so the regional minima moved
between samples — but as a sum of per-region minima it does not establish that
any comparable total or DMA block dipped below 19 456, and transient DMA
behaviour between samples remains unmeasured. Any soak that watches the sampled
figure alone will not see the minima advance.

**Discarded hypothesis, kept as written for the record.** The first reading
tied this to the relay finding above: the old image did zero TLS and held a
40 960 B block with a 76 435 B internal-free low-water, the new image runs a
TLS handshake every 2.5 s, and mbedTLS session buffers are internal and
DMA-capable, so the churn would fragment the pool the display flush allocates
from, with the LVGL lock failures clustering near the low-water drops. The
interval analysis in OBS-37 found it unsupported the same night: the handshakes
precede everything because they happen every 2.5 s, so an immediate mechanism
has no support; the lock failures and the memory figure are two separate
signals, and no trigger was identified for any low-water step. Delayed
contention is not ruled out by timing alone — excluding TLS needs a build with
every TLS client off — so do not start from TLS, and do not strike it either.

The overlays are not implicated: all three report `internt +0 B`, and the
condition was already present before SETTINGS was ever opened.

This is what the evening produced in place of §5, which was not run (its
dozen SETTINGS cycles under watch were never done as §5). It should be treated
as a measurement gap to instrument on `v1.0.0-67` — the DMA block's own minimum is
not tracked, so the transient is inferred from a total — not as a run-sheet
checkbox and not as a measured threshold breach.


## The OTA window measured, three cycles

Run deliberately as part of the OBS-37 investigation, not as manual-test
4.1/4.2. The user held KEY3 for 3 s, chose UPDATE, waited for a heap reading,
then short-tapped to close.

| | cycle 1 | cycle 2 | cycle 3 |
|---|---|---|---|
| window open for | ~55 s | 141 s | ~50 s |
| internal free before | 55 079 | 65 075 | 55 075 |
| internal free, stable while open | ~47 970 | ~47 960 | ~47 970 |
| largest block while open | 17 408–18 432 | 16 384–23 552 | 17 408–21 504 |
| internal free after close | 56 859–58 879 | 55 087–68 843 | — |
| low-water before -> after | 10 179 -> 9 623 | 9 623 -> 9 623 | 9 623 -> 9 623 |

While open, internal free sits at ~47 965 in all three cycles, within ten
bytes, regardless of how long the window is open and whatever the pre-open
figure was; the pre-open figure oscillates by ~10 kB on its own, so the cost is
not a fixed delta ("~7 kB" is cycle 1's difference only). Free returned to the
pre-open band after cycles 1 and 2; cycle 3 has no post-close reading. A fixed per-open cost is disproved
(cycles 2 and 3 moved the low-water by zero); cycle 1's 10 179 -> 9 623 drop
leaves an intermittent or timing-dependent effect unproven, not refuted.

## The low-water walk, unexplained

```
t=13 s    44199        t=843 s   11191
t=23 s    43935        t=935 s   11143
t=33 s    19167        t=2129 s  11139
t=340 s   18991        t=2164 s  10179
t=360 s   18795        t=2506 s  9623
t=605 s   18451
t=625 s   18371
```

Twelve readings, eleven downward steps, over roughly 45 minutes, ending at a summed low-water of
9 623 B, under the 11 520 B a display flush needs — a comparison that, per the
note above, is not a statement about any single instant. No trigger is identified for any single step. The
sampled `heap:` figure never went below 16 384 across the whole session, so none
of this is visible in the number a soak would normally watch. This is the open
question OBS-37 carries forward.

## The window that cannot be attributed — and why that is the finding

Two maintenance windows opened at t=837 s and t=2156 s, before the three
measured cycles. Asked whether he opened them, the operator answered: **no — he
did not see it and was neither awake nor at the screen.**

Both *automatic* open paths are excluded by the log:

| Path | Condition | Result |
|---|---|---|
| Boot self-rearm (`ota_service.c:565–578`) | running partition must be `ESP_OTA_IMG_PENDING_VERIFY` | **no** — `boot-health: hälsogrinden vilar: ota_0 i tillstånd 0x2 (ej väntande)`, and zero `nyss uppdaterad avbild` lines |
| Notice answered with JA (`ota_service.c:~446`) | the UPDATE READY pill is tapped | **no** — zero `notisen besvarad med JA` lines |

The firmware is explicit that our case should stay closed: *"En esptool-flashad
boot (UNDEFINED/VALID) har inget föregående håll och lämnas stängd."* That rule
held — the window did not open at boot.

**But the question cannot be settled, because KEY3 presses are not logged.** A
3 s hold into SETTINGS followed by a tap on UPDATE (`main/main.c:751`) leaves no
trace at all except the window-open line itself. The absence of preceding lines
before t=837 s therefore proves nothing.

What the log does show is that someone was at the panel shortly before the
*second* window:

```
I (2142567) needs-you-net: skickade deny                        <- panic, i.e. a ~2 s KEY3 press
I (2156363) ota-service: underhållsfönstret öppet i tio minuter
```

Fourteen seconds apart. The panic only appears in the log because it sends a
network message; the gesture itself is invisible.

So the honest finding is not "a window opened by itself". It is that **the
consent model has no audit trail.** `CLAUDE.md` calls it non-negotiable that the
maintenance window opens only from the device, and the panel cannot afterwards
show that it did. A window open, its trigger source (hold-into-menu, notice pill,
or boot re-arm), and the closing event should each be logged; without that, an
open window is unattributable after the fact and this exact question is
unanswerable — as it is here.

## `.ota-device` points at the wrong address

```
.ota-device:        192.168.x.A   (redacted; a stale lease)
panel's actual IP:  192.168.x.B   (redacted; esp_netif_handlers, t=9.2 s)
```

`tools/ota-flash.sh "$(cat .ota-device)"` would have sent to an address the
panel does not hold. Had §1 gone over the air as the run sheet intends, this
would have surfaced as a second failure — after the missing OTA token, and after
the user had already stood at the panel and opened a window. The file is
git-ignored and was not modified. It must be corrected before §3 and before any
future OTA delivery.

## What was not run

Nothing in `docs/manual-test-key3.md` was walked as written. Specifically:

- **§1 gesture (1.1–1.5)** — not run. The 1.2 timing the run sheet asks to be
  recorded (how long the ~2 s press lasted) was never measured, and the
  2026-08-16 class of bug it guards against is therefore unretested on this
  image.
- **§2 without a network (2.1–2.8)** — not run. The AP was never taken down.
  2.4 (ABOUT shows a dash, not `0.0.0.0`) and 2.8 (no leftover QR over
  `NO NETWORK`, the newest fix and the least proven) remain unverified, as does
  the 2.7 self-open timing.
- **§3 menu vs takeover (3.1–3.6)** — not run. No staged build was made; no
  `build-stage/` exists. 3.3's timing (how soon after the takeover the short tap
  came) was not measured.
- **§4.1–4.3** — not run. The three measured window cycles above are not a
  substitute: they exercised open and close, but not the menu route's pass
  criteria, the ten-minute countdown, the `KEY3 CLOSES` footer, or 4.3's
  hold-inside-an-open-window shortcut to WIFI SETUP.
- **§3.5 Codex smoke test** — not run, by decision, with four blocking `doctor`
  FIX items outstanding.
- **manual-test 4.4 and 4.5** — not exercised, as recorded above.
- **§5 soak** — a six-hour unattended passive observation ran 02:03–08:03 and
  completed with no alarm. It is **not** the run sheet's §5, which also requires
  a dozen SETTINGS opens under watch; those were done earlier as part of the
  OBS-37 investigation, not as §5. Results are in OBS-37: the low-water
  plateaued at 9 355 (268 bytes from the last measured cycle's 9 623, 36 bytes
  across the six hourly readings, against 34 576 bytes in the first 45 minutes), lock failures ran 14, 4, 0, 0, 1, 0 per hour with nobody at the panel —
  declining after hour 2, with those 19 unattended failures keeping the
  background tasks in scope — and the sampled block range held at 18 432–31 744
  (hour 4 touched 18 432; the session's 16 384 belongs to the earlier deliberate
  window cycles). The panel was still
  drawing at ~7.2 hours uptime.

## Host state at end of session

The Mac's free disk fell from 1.5 GiB to **251 MiB** during the evening because
`~/.cache/codex-runtimes` (~1.6 GB) was re-downloaded by a running Codex after
being deleted earlier in the session. Deleting it again would repeat. The soak's
own footprint is ~1 MiB over six hours and fits, but the margin against anything
else growing overnight is thin, and the soak logs free disk each hour and flags
a drop below 100 MiB.

`spec/device-units.yaml` was updated to `installed_firmware:
v1.0.0-67-ge51b79f` and `last_physical_verification: "2026-09-06"`.

Nothing was committed that evening. This PR later changed `README.md`'s
SETTINGS section: it said no panel had been flashed with the menu, which stopped
being true with this flash, so it now says the menu is on the panel but
unreviewed. §3 has still not passed — it has not been attempted.
