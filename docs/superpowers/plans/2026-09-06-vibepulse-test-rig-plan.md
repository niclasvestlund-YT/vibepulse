# VibePulse test rig: drive the panel without a finger on KEY3

> **For agentic workers:** this is a plan, not an authorization. Nothing in
> it flashes a board. A rig image goes on the glass only when the user says
> so in the local session, like any other image, and comes off again the
> same way.

**Goal:** let a local Claude Code session run `docs/manual-test-key3.md`
§1, §3 and §4 — and most of §2 — on the physical panel with no human at
the button, and check what the glass shows against the same landmarks the
simulator is held to.

**Why now (2026-09-06):** the first SETTINGS flash needed a person holding
KEY3 at 01:00 for tests that take two minutes each and a soak that takes
hours. The person went to bed with §1–§4 unrun. The soak ran alone because
nothing in it needs a hand; the rest could not. This plan removes that
asymmetry without touching the consent model of the image people actually
run.

**Architecture in one paragraph:** a Kconfig-gated component,
`torget_testrig`, that exists only in a *rig image*. It fakes exactly two
physical inputs at the point where the host reads them — the KEY3 GPIO
level and the touch indev — and can drop the Wi-Fi association on command,
take an LVGL snapshot into PSRAM and stream it over the USB-JTAG serial
port. Commands arrive on that same serial port. The button policy, the
arbitration, the menu and every window stay byte-identical to production:
the rig only pretends a finger did something, so the consent chain is still
walked through the real UI. A host tool on the Mac speaks the protocol,
replaces `idf.py monitor`, and runs the manual as a script. The simulator
accepts the same commands, so one script drives both and the simulator
stays the spec.

**Tech stack:** C11 / ESP-IDF 5.5 / LVGL 9.5 (`LV_USE_SNAPSHOT` is already
on in the simulator's `lv_conf.h`), Python 3.11+ stdlib + `pyserial`,
Pillow for landmarks, the existing `test/test_vibepulse_visual_landmarks.py`.

---

## Non-negotiables (read before writing code)

1. **Production images are unchanged.** `CONFIG_TORGET_TESTRIG` defaults to
   `n`, is not in `sdkconfig.defaults`, and a host test asserts both. The
   component directory is not compiled at all when the option is off — no
   dead code paths in the shipped binary.
2. **A rig image announces itself and cannot be advertised as an update.**
   The app descriptor version gets a `+testrig` suffix.
   `tg_notice_version_is_newer()` rejects anything after the hash that is
   not `-dirty`, so a rig `torget.bin` in `build*/` is never turned into an
   UPDATE READY takeover on a production panel, and `tools/ota-flash.sh`
   cannot read a commit out of it and refuses. The boot banner and the
   ABOUT row show `TESTRIG`. A pinned host test proves the suffix is
   unparseable by the notice policy.
3. **The rig opens nothing by itself.** There is no `open_window` command.
   The only way to the maintenance window in a rig image is `key3 hold
   3000` → `touch <UPDATE row>` — the same two acts a person performs. The
   arbitration's "no output opens the window" invariant
   (`test/test_key3_arbitration.c`) is untouched and still runs.
4. **Serial only.** No HTTP, no new port: port 80 is contended by the two
   windows, and §2 runs with the network gone. The USB-JTAG CDC link is
   already the monitor and already resets the board on connect (verified
   2026-09-05: `rst:0x15`, USB-reset (11)) — the rig tool documents that
   and starts every session from a fresh boot on purpose.
5. **Nothing internal.** Snapshot and stream buffers live in PSRAM; the rig
   logs its own `overlaykostnad testrig:` line at boot like the three
   overlays do, and the internal figure must be `+0 B`. The DMA low-water
   drift under investigation as OBS-35 must not get a new suspect.
6. **A rig image never stays on the shelf unit.** The close-out of any rig
   session flashes a production image back (OTA works: the rig image has
   the OTA token) and records both in `spec/device-units.yaml`.

## File map

- Create `components/torget_testrig/CMakeLists.txt`,
  `Kconfig` (`TORGET_TESTRIG`, default n, help text = non-negotiables 1–3).
- Create `components/torget_testrig/testrig_proto.c/.h`: pure command
  parser and reply formatter. No ESP-IDF includes, so it compiles in the
  host test runner and in the simulator.
- Create `components/torget_testrig/testrig.c/.h`: the device side —
  serial reader task, fake KEY3 level with expiry, touch injection wrapper
  (chains the indev read callback the way `main/rotation.c` already does),
  `wifi drop` / `wifi rejoin`, `frame` (snapshot → PSRAM → base64 lines).
- Modify `main/main.c` at the KEY3 read (`gpio_get_level(GPIO_NUM_18) == 0`):
  `|| tg_testrig_key3_down()` under `#if CONFIG_TORGET_TESTRIG`. One line.
  The arbitration call below it does not change.
- Modify `main/main.c` where `sg_rotation_start(touch)` is called: install
  the rig's touch wrapper *after* rotation so injected points are in
  panel coordinates and go through the same rotation the real ones do.
- Modify `main/CMakeLists.txt` version embedding: append `+testrig` when
  the option is on.
- Modify `sim/main.c`: accept the same commands on stdin (behind a
  `--rig` flag) by calling the same `testrig_proto` parser and mapping to
  the existing `poll_keys` state and a synthetic pointer.
- Create `tools/testrig/rig.py`: host tool. Subcommands `monitor`,
  `key3 tap|down|up|hold <ms>`, `touch <x> <y> [ms]`, `wifi drop|rejoin`,
  `frame <out.bmp>`, `heap` (parses the periodic `heap:` line). Logs the
  full serial stream to a file while it runs, so it replaces `idf.py
  monitor` rather than competing with it.
- Create `tools/testrig/run_manual_key3.py`: the manual as code. Each row
  of `docs/manual-test-key3.md` §1, §3, §4 becomes a step with a rig
  action and a landmark expectation; §2 rows that only need the STA gone
  use `wifi drop`. Emits a Markdown table in the review's format, with
  `not run` for rows it cannot do (the AP-off rows, the Codex smoke test).
- Create `test/test_testrig_proto.c` (parser table, malformed input,
  expiry arithmetic) and `test/test_testrig_gating.py` (option off in
  `sdkconfig.defaults`; `+testrig` unparseable by
  `tg_notice_version_is_newer`; `LIVE_DOCS` guard still green with the
  new doc).
- Create `docs/testrig.md`: how to build a rig image, what it can and
  cannot do, the reset-on-connect fact, and the close-out rule.
- Modify `docs/manual-test-key3.md`: one paragraph at the top pointing to
  the rig for the rows it covers, keeping the file the authority on what
  "expect" means.

## Protocol (serial, line-based, ASCII)

Requests are one line, replies are one line starting with `rig:`; the
firmware's ordinary log keeps flowing in between and the host tool
separates them by prefix.

```
key3 tap                 -> rig: key3 tap ok
key3 hold 3000           -> rig: key3 hold 3000 ok      (level low for 3000 ms, then released)
key3 down | key3 up      -> rig: key3 down ok           (manual, with a 10 s safety expiry)
touch 240 300 [80]       -> rig: touch 240 300 80 ok    (pressed for 80 ms at panel coords)
wifi drop | wifi rejoin  -> rig: wifi drop ok           (esp_wifi_disconnect / reconnect)
frame                    -> rig: frame 480 480 rgb565 <n> lines
                            rig: f <base64...>   x n
                            rig: frame end <crc32>
heap                     -> rig: heap <free> <largest-dma> <lowest>
ping                     -> rig: pong <version> TESTRIG <uptime-s>
```

Timing lives on the device (`key3 hold` counts on the device clock, not
across USB latency), so the 1.2 "~2 s press must open nothing" row is a
real test of the policy's thresholds, not of the host's scheduler.

## Landmarks on the real glass

`frame` gives a 480×480 RGB565 raster. The host converts it to the same BMP
shape the simulator captures produce and runs the existing landmark
checks against it. First pass: the landmarks only (menu rows present,
footer text, ring present, QR present/absent, NO NETWORK page). Pixel-exact
comparison with simulator captures is a stretch goal: the renderer is the
same LVGL, but the panel has burn-in drift offsets, so exact equality is
expected only at drift offset zero and is not a gate in this plan.

## Phases

### Phase 1 — button and touch, no pictures (one evening)

- [ ] Component skeleton, Kconfig, one-line hook at the GPIO read, version
      suffix, `ping`, `key3 *`, `heap`.
- [ ] Touch wrapper chained after rotation; `touch x y ms`.
- [ ] Simulator accepts the same lines on stdin under `--rig`.
- [ ] `test_testrig_proto.c` and `test_testrig_gating.py` in `test/run.sh`.
- [ ] `rig.py` with `monitor`, `key3`, `touch`, `heap`.
- [ ] Physical: rig image on the unit, `key3 hold 3000` opens SETTINGS,
      `touch` on ABOUT opens ABOUT, `key3 tap` closes. Recorded as a rig
      review; a production image flashed back afterwards.

### Phase 2 — seeing the glass (one evening)

- [ ] `frame` with snapshot into PSRAM, base64 stream, CRC.
- [ ] `overlaykostnad testrig:` boot line; internal must read `+0 B`.
- [ ] Host conversion to BMP, landmark checks reused from
      `test_vibepulse_visual_landmarks.py` (refactor the landmark functions
      out of that test into a small module both can import).
- [ ] `wifi drop` / `wifi rejoin`.

### Phase 3 — the manual as a script

- [ ] `run_manual_key3.py` covering §1 (all), §2 (rows 2.1–2.6 and 2.8 via
      `wifi drop`; 2.7's 120 s self-open included), §3 (with the staged
      `build-stage/` trick from `docs/flash-session-2026-09.md` for 3.4,
      3.6 before 3.5), §4.1–4.3. Output is the review table.
- [ ] Nightly-capable: the script can run unattended after a rig flash and
      leave a report; the soak (§5) is already unattended.
- [ ] `docs/testrig.md`, the pointer paragraph in the manual, CHANGELOG.

## What this does not solve

- **The physical button itself.** The rig fakes the GPIO level after the
  pin is read; a dead or bouncing switch is invisible to it. One real press
  per session (recorded) keeps that honest.
- **The AP-off rows** that need the router's radio gone rather than the
  STA disconnected. `wifi drop` is a good proxy for "no network"; it is
  not the same as "no AP on the air", and the review says which was used.
- **The Codex smoke test** (§3.5). That needs a real permission prompt and
  a real APPROVE; the rig can press APPROVE, but the pass criterion is the
  round trip in Codex, which stays a person's call.
- **Consent for the rig image.** A rig image can be driven over serial to
  open the maintenance window. That is the point, and it is why the image
  is marked, unannounceable, and never left on the shelf.

## Open questions for the user

1. Is a `+testrig` suffix acceptable in the banner and ABOUT, or should the
   glass carry a visible corner marker as well?
2. Nightly unattended runs: on the shelf unit, or a second board on the
   desk? A second unit makes non-negotiable 6 moot and the physical-button
   caveat smaller.
