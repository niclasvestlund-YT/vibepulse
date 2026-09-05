# Flash session 2026-09: bring the panel up to main

A run sheet for the *local* Claude Code session on the Mac that has the
panel on USB and `.ota-device` in the repo root. A cloud session cannot
reach the panel, the serial port or the tokenserver; it can only prepare
this list. Nothing here authorizes a flash by itself — the user says
"flash" in the local session, and that session does it.

## Why this session exists

`spec/device-units.yaml` says `torget-home-01` runs `v1.0.0-25-g054db68`
(flashed 2026-08-30). `main` is roughly 40 commits past that. What the
panel has never seen:

| Change | Where it landed | What only the glass can prove |
|---|---|---|
| SETTINGS menu on a 3 s KEY3 hold (UPDATE / WIFI / ABOUT) | #72, #73 | timing, z-order against NO NETWORK, the takeover hand-off |
| Overlay cost lines at boot (`overlaykostnad …`) | #77 | the actual `settings` internal-RAM figure — the FEATURES row waits on it |
| Setup QR no longer outlives its window | #76 | no leftover QR over `NO NETWORK` |
| KEY3 arbitration moved to pure platform code | #73 | no behaviour change — a regression here would be a bug |
| `claudeSourcePresent`, Codex-only host start, body drain | #69–#71 | host side; flashed panels tolerate them by design |

## 0. Preflight (before any build)

1. **Both docs PRs are merged**: #83 (README frame-drift guard) and #84
   (OTA runbooks say the right gesture). Neither touches firmware, but #84
   fixes the line `tools/ota-flash.sh` prints while you stand at the panel.
2. On the Mac, in the repo that `.ota-device` lives in:

   ```sh
   git checkout main && git pull origin main
   git describe --tags --always --dirty      # must NOT end in -dirty
   git status --short                        # empty
   ```

   Write the version down. It is compared against `otaAvailableVersion`
   and the booted banner later. The sender refuses `-dirty` on purpose;
   never set `TG_OTA_ALLOW_DIRTY` without the user saying so.
3. Tokenserver: if anything under `tools/tokenserver/` changed since it
   was last restarted (it has — #69, #70, #71, #82), restart it:

   ```sh
   launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver
   ```

   Then `python3 tools/vibepulse_setup.py doctor` — it now names a saved
   Codex mode that would silently hide permission cards (#82).
4. Panel on **its own USB power supply**, not the Mac's port. The Mac port
   cannot run the AMOLED and it looks exactly like a bad cable.
5. Open a serial monitor before flashing so the boot log is captured:

   ```sh
   idf.py -p $(ls /dev/cu.usbmodem* | head -1) monitor
   ```

## 1. Build and deliver (flash authorization required)

```sh
. ~/esp/esp-idf/export.sh
idf.py build
tools/ota-flash.sh            # waits; prints the newest build*/torget.bin it will send
```

Then, **at the panel**: hold KEY3 a full 3 s → SETTINGS → tap **UPDATE**.
The uploader only starts when the window is open; the hold alone reaches
the menu, nothing more. Expect RECEIVING → VERIFYING → RESTARTING, then
the boot-health gate. After the reboot the window re-arms once, so a
second build in the same sitting needs no new hold. A short KEY3 press
ends the chain when you are done.

If the panel shows **UPDATE READY** the moment it boots, the booted image
is older than what the tokenserver advertises — compare the banner with
the version from §0 before doing anything else.

USB (`idf.py -p <port> flash`, BOOT held + RESET) stays the rescue path
if the image does not boot. It needs its own go-ahead.

## 2. Read the boot log (2 min, do it first)

Three lines appear once, early:

```
overlaykostnad wifi-setup: LVGL-pool +N B (pool U/T använt), internt ±N B (kvar F)
overlaykostnad settings:   ...
overlaykostnad ota:        ...
```

Copy all three into the review. The `settings` internal figure is the
budget the AMOLED rule asks for: `+0 B` retires the internal-RAM worry
with evidence; anything else is the number FEATURES has to fit under.
Also note the first `heap:` line (internal free, largest DMA block).

## 3. The panel checks, in order

Run `docs/manual-test-key3.md` as written. The order below is only so the
network-off part is done once, not twice.

1. **§1 gesture** (2 min) — 1.2 is the one to be careful with: a ~2 s
   press must panic and open *nothing*.
2. **§2 without a network** (5 min, AP off) — 2.4 (ABOUT shows a dash, not
   `0.0.0.0`) and 2.8 (no leftover QR over NO NETWORK) are the two least
   proven.
3. **§3 menu vs takeover** (5 min) — 3.1 (menu stays above the NO NETWORK
   redraw) and 3.5 (LATER must not bring the menu back). **Do not have
   `tools/ota-flash.sh` running here**; a leftover copy uploads the moment
   a window opens.
4. **§4 update path** — already exercised by §1 of this sheet; only 4.3
   (hold–hold shortcut inside an open window) and 4.5 (re-arm once) still
   need a look.
5. **Codex smoke test** — `docs/agent-setup.md`, "Post-flash physical Codex
   smoke test": one `mcp__vibepulse__ask` with header `Test`, question
   `Ser du APPROVE?`, `Ja` recommended. Pass only on a real APPROVE tap
   returning `answered`, `option_index: 0`. LEAVE IT, timeout or the
   computer fallback is not a pass.
6. **§5 soak** (30+ min) — `heap:` every 10 s, DMA largest block steady;
   open and close SETTINGS a dozen times and confirm the pool figure does
   **not** climb per open.

## 4. Close out (same session, before the shelf gets it back)

- `spec/device-units.yaml`: set `installed_firmware` to the booted
  `git describe` and `last_physical_verification` to today.
- Write the review under `docs/superpowers/reviews/` with the outcome in
  the first line, the way the 2026-08-30 one does. Name every step that
  was *not* run.
- Failures → `docs/observability-backlog.md`; root causes →
  `docs/lessons.md`. Note how long a hold actually lasted for 1.2, 2.7
  and 3.3.
- README's SETTINGS section still says no panel has been flashed with the
  menu. Once §3 passes, that sentence changes; do it in the same commit
  as the inventory update.
