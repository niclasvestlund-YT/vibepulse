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
| SETTINGS menu on a 3 s KEY3 hold (UPDATE / WIFI / ABOUT) | #72, #73 | timing, z-order against NO NETWORK, the takeover hand-off. The flashed image predates the menu: its hold opens the update window directly, which is how the first delivery in §1 goes |
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
4. **Power and serial at the same time.** The panel must run from its
   own power supply (a Mac port cannot run the AMOLED; it looks exactly
   like a bad cable) *and* the Mac must still see `/dev/cu.usbmodem*` to
   capture the boot log. That takes a powered USB hub or a PSU/data-split
   cable, not a plain wall charger. `docs/lessons.md` (2026-08-13) records
   serial-monitoring a *running* board as unverified, so this is the
   first thing to confirm: with the panel booted on that arrangement,
   `ls /dev/cu.usbmodem*` must list a port. If it never appears, §2
   cannot be done this session; the three lines are logged once at boot
   and nowhere else. Say so in the review rather than skipping §2 quietly.
5. **Find the panel's port, then open the monitor.** Source ESP-IDF in
   every terminal you use, the monitor one included — `idf.py` does not
   exist in a fresh shell:

   ```sh
   . ~/esp/esp-idf/export.sh        # their install path may differ
   ls /dev/cu.usbmodem*             # note the list
   ```

   Now unplug the panel's data cable, list again, plug it back, list a
   third time: the entry that vanishes and returns is the panel. Use that
   exact path, not the first match — a Mac with another serial device
   would otherwise monitor the wrong thing and lose the once-only lines:

   ```sh
   idf.py -p /dev/cu.usbmodemXXXX monitor
   ```

## 1. Build and deliver (flash authorization required)

In a second terminal (sourced the same way):

```sh
ls -d build*/                 # only build/ should be here; remove any leftover build-stage/ first
idf.py build
tools/ota-flash.sh "$(cat .ota-device)" build   # the build dir is pinned on purpose
```

The build directory is passed explicitly because the script otherwise
takes the newest `build*/torget.bin` by mtime, and it makes that choice
only after the window opens — too late for you to see it. A stale
`build-stage/` from an earlier session would win and be sent.

**The first delivery lands on the old image, and the old image has no
SETTINGS menu.** On `v1.0.0-25-g054db68` the 3 s KEY3 hold opens the
update window directly; the menu, and UPDATE inside it, arrive with this
build. So for this one upload: hold KEY3 a full 3 s, the UPDATES ON ring
appears, and the uploader (already polling) sends. Expect RECEIVING →
VERIFYING → RESTARTING, then the boot-health gate. The script exits after
the one upload.

After the reboot the window re-arms itself once (manual-test **4.5**).
Nothing is armed on the Mac now, so nothing uploads. Short-tap KEY3 to
close it.

Now the new image is up and the rest of `docs/manual-test-key3.md` §4
runs with **nothing polling on the Mac** — the uploader would otherwise
send the instant 4.1 opens a window, and 4.2 would never happen:

| # | At the panel | Expect |
|---|---|---|
| 4.1 | Hold KEY3 a full 3 s → SETTINGS → tap **UPDATE** | the window opens, ten-minute countdown, `KEY3 CLOSES` |
| 4.2 | Short-tap | it closes early, with no upload |
| 4.3 | Reopen the same way, then hold 3 s inside the already-open window | it switches to WIFI SETUP (the hold–hold shortcut); short-tap to close |
| 4.4 | Only if a real second build exists — a *pushed* commit with green CI, since the uploader's CI gate refuses anything else: start `tools/ota-flash.sh "$(cat .ota-device)" <that build dir>`, then hold 3 s → SETTINGS → tap **UPDATE** | the upload runs through the menu's row, the first time that path is used on the glass. Otherwise record 4.4 as not exercised |

That is all of §4. A short KEY3 press ends the re-armed chain whenever
you are done.

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

   **3.4 needs a strictly newer version than the one now running**, and
   after §1 the newest `build*/torget.bin` on the Mac *is* the running
   one, so nothing would announce. The firmware orders versions by the
   commit distance in `git describe`, so stage one more commit and build
   it into its own directory:

   ```sh
   git commit --allow-empty -m "stage: one commit ahead for manual-test 3.4"
   idf.py -B build-stage build      # clean tree, so not -dirty
   ```

   The tokenserver advertises the newest `torget.bin` by mtime within
   30 s; the panel sees a distance one higher and shows UPDATE READY
   (3.4). **Do 3.6 before 3.5**: hold 3 s *while the notice is showing*
   and confirm nothing happens, *then* tap LATER. LATER starts a one-hour
   snooze, so the other order leaves 3.6 waiting an hour.

   **This staged image is announcement-only.** The uploader's CI gate
   refuses a commit without a green run on GitHub, and this one is local
   and unpushed; do not push it, and do not set `TG_OTA_ALLOW_NO_CI` to
   get past the gate — that is the user's call, not the sheet's. So 4.4
   is not exercised by it. **Before you leave:** confirm with `git log -1`
   that the empty stage commit is still HEAD, drop it with
   `git reset --soft HEAD~1` — *soft*, so the review notes and inventory
   edits from §4 that may already be in the working tree survive — and
   delete `build-stage/` separately, or the panel nags about a phantom
   version every hour from then on.
4. **§4 update path** — covered by §1 of this sheet: 4.5 right after the
   first delivery, then 4.1–4.3 on the new image with nothing armed, and
   4.4 only if a second build goes on. Nothing left here.
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
