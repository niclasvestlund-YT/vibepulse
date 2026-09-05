# Manual test: the KEY3 flow on the panel

Everything in this file needs the physical unit. It is deliberately short,
because most of the flow is already proven without hardware and repeating
that here would waste the one resource a script cannot replace: your
attention on the glass.

**What is already automated, and does not need you:**

| Proven by | What it covers |
|---|---|
| `test/test_key3_arbitration.c` | the eight consent/escape invariants, each pinned alone |
| `test/test_key3_flow.c` | the whole journey as a sequence, with each tick's output fed back as the next tick's input, plus the same-tick takeovers |
| `test/test_vibepulse_visual_landmarks.py` | what the glass actually renders in each state, from the pixels |
| `test/test_ota_gesture_docs.py` | that the docs describe the gesture the firmware actually implements |

**What only the panel can settle:** timing that a pure function cannot model,
memory under real load, and whether the thing feels right in the hand. That
is what follows.

> **Flashing is a separate, explicit authorization.** Nothing in this file
> authorizes one. Run it only on a build you already decided to install.

---

## 0. Before you start

Read the boot log. Since the overlay-budget change, three lines appear once,
early:

```
overlaykostnad wifi-setup: LVGL-pool +N B (pool U/T använt), internt ±N B (kvar F)
overlaykostnad settings:   ...
overlaykostnad ota:        ...
```

**Write the `settings` line down.** It is the measured memory budget the
AMOLED rule requires for a persistent layer, and this is the flash that
answers it. Two things to check:

- **The internal figure.** If it is `+0 B`, the menu costs no internal RAM
  at all and the starvation concern is retired with evidence. If it is not
  zero, that number *is* the budget — compare it against the lowest-ever
  internal figure in the periodic `heap:` line under real load.
- **The pool figure** against the pool total. LVGL's pool is 256 KiB in
  PSRAM; a menu taking a noticeable slice of it is worth knowing even
  though PSRAM is not the scarce resource.

---

## 1. The gesture itself (2 min)

| # | Do | Expect |
|---|---|---|
| 1.1 | Short-tap KEY3 | the app changes. No menu. |
| 1.2 | Press and hold ~2 s, release | Needs You panic fires. **No menu, no update window.** |
| 1.3 | Hold a full 3 s | SETTINGS appears: UPDATE, WIFI, ABOUT, footer `KEY3 CLOSES` |
| 1.4 | Short-tap while the menu is up | the menu closes |
| 1.5 | Hold 3 s again, then hold 3 s again | the menu opens, then closes. It never stacks on itself. |

**1.2 is the one to be careful about.** A ~2 s press once panicked *and*
left a window stuck. If a press between roughly 1.5 s and 3 s ever opens
anything, stop and report it — that is the 2026-08-16 class of bug.

## 2. Without a network (5 min, needs the AP off)

Take the panel off its network, or power the AP down.

| # | Do | Expect |
|---|---|---|
| 2.1 | Wait ~60 s | the glass names the network it is hunting and what the radio answered — not dashes |
| 2.2 | Hold 3 s | SETTINGS opens, **UPDATE visibly dimmer than WIFI and ABOUT** |
| 2.3 | Tap UPDATE | **nothing happens.** No window, no flicker. |
| 2.4 | Tap ABOUT | ADDRESS shows a dash. Not `0.0.0.0`, not blank. |
| 2.5 | Tap BACK, tap WIFI | the setup window opens with its QR |
| 2.6 | Short-tap | the setup window closes |
| 2.7 | Leave it alone. If you closed a window in 2.6, the wait is **120 s from that close**, not 90 s from the network loss — `tg_wifi_setup_should_open()` holds a cooldown so a closed window cannot immediately reopen itself | the setup window opens **by itself** |
| 2.8 | While it is open, short-tap | it closes — and the `NO NETWORK` page behind it has **no leftover QR code** on it |

**2.8 is the newest fix and the least proven.** A stale QR over `NO NETWORK`
invites someone to scan a code for an access point that no longer exists.

## 3. The menu against a window taking over (5 min)

This is where the three #72 bugs lived. Each step needs the menu **already
open** when the other thing happens.

| # | Do | Expect |
|---|---|---|
| 3.1 | With no network, hold 3 s to open SETTINGS, then wait through a `NO NETWORK` redraw or two | the menu **stays on top**. It must not slide behind the NO NETWORK page. |
| 3.2 | With SETTINGS open, wait for the auto-open — again 120 s from the last close if §2 ran first, otherwise 90 s from the network loss | the menu disappears as the setup window arrives — not layered, not behind |
| 3.3 | Immediately after 3.2, short-tap | the **setup window** responds. The press must not be swallowed by the menu that just left. |
| 3.4 | Reconnect. Open SETTINGS. Make the computer *announce* a newer build — put a newer `build*/torget.bin` where the tokenserver reads it, so `otaAvailableVersion` changes. **Do not start `tools/ota-flash.sh`.** | the UPDATE READY takeover arrives and **the menu is gone**, not behind it |
| 3.5 | Tap LATER | you return to the apps. **The old menu must not reappear.** |
| 3.6 | Hold 3 s while the takeover is showing | **nothing at all.** Deliberate: answer the takeover with its pills. |

> **Do not have `tools/ota-flash.sh` running during §3.** It polls
> `/api/ota/status` and uploads the instant `maintenance_open` turns true,
> so a copy left over from announcing the build would flash the panel the
> moment you open the window in §4.1 — before you reach the step where you
> decided to install. Announcing and uploading are separate acts here;
> the script only ever starts at §4.4.

## 4. The update path (10 min, flash authorization required)

Only run this if you are installing the build.

| # | Do | Expect |
|---|---|---|
| 4.1 | Hold 3 s, tap UPDATE | the window opens, ten-minute countdown, `KEY3 CLOSES` |
| 4.2 | Short-tap | it closes early |
| 4.3 | Reopen, then hold a full 3 s **inside** the window | it switches to WIFI SETUP (the hold–hold shortcut) |
| 4.4 | Reopen and run `tools/ota-flash.sh <ip>` | RECEIVING → VERIFYING → RESTARTING, then the boot-health gate |
| 4.5 | After the reboot | the window re-arms **once** — a second build installs with no new hold |

## 5. Leave it running (30+ min)

Let it sit on the shelf doing nothing.

- The `heap:` line every ~10 s: does the **largest DMA block** hold steady?
  Its collapse predicted the 2026-08-06 freeze.
- Open and close SETTINGS a dozen times over the session. The tree is built
  once at boot and reused, so the pool figure must **not** climb with each
  open. If it does, something is allocating per-open and the budget in §0 is
  wrong.

---

## Reporting

A failure here matters more than anything in CI, because it is the only
evidence that touches the real thing. Note the step number, what the glass
did, and the log around it — and if it is one of the timing steps (1.2, 2.7,
3.3), note how long you actually held.

Findings go to `docs/observability-backlog.md`; anything with a root cause
worth remembering also earns an entry in `docs/lessons.md`.
