# Over-the-air updates — how the whole loop works

This is the complete story of how a build on your Mac becomes firmware on
the shelf, and why each step looks the way it does. The README has the
short version; this is the reference.

## The loop at a glance

```
 Mac                                   screen
┌──────────────────────────┐          ┌─────────────────────────────┐
│ idf.py build             │          │                             │
│  └─ torget.bin           │          │  polls /api/tokens every    │
│ tokenserver announces    │ ───────► │  30 s, compares versions    │
│  otaAvailableVersion     │          │  └─ mismatch: UPDATE READY  │
│                          │          │     takeover on the glass   │
│ tools/ota-flash.sh waits │          │                             │
│  for the window...       │          │  YOU consent:               │
│                          │          │   · tap UPDATE pill, or     │
│                          │          │   · hold KEY3 ~3 s, then    │
│                          │          │     pick UPDATE in SETTINGS │
│  └─ POST firmware ─────────────────►│  window open 10 min         │
│     (token + SHA-256)    │          │  RECEIVING → VERIFYING →    │
│                          │          │  RESTARTING → reboot into   │
│                          │          │  the other slot, health     │
│                          │          │  gate approves or rolls back│
└──────────────────────────┘          └─────────────────────────────┘
```

## The consent model (why a button/tap at all)

Nothing can write firmware to the screen without three independent factors:

1. **Physical presence** — the maintenance window opens only from the
   device itself: a ~3 s KEY3 hold followed by **UPDATE** in the SETTINGS
   menu, or a tap on the UPDATE pill when the UPDATE READY takeover is
   showing. A script, a LAN neighbour, or a compromised Mac cannot open
   it remotely. On the takeover, the UPDATE pill is the *only* yes — a
   tap anywhere else (including LATER) snoozes, so an accidental touch
   always lands on the safe side.

   The hold used to guess which window you wanted from whether the panel
   had an IP. It now opens **SETTINGS** and lets you say, which changes
   nothing about consent: the menu is reachable only from the device, so
   physical presence is still required, and the token and the ten-minute
   window are untouched. Two consequences worth knowing:

   - **Without an IP, UPDATE is greyed out and cannot be picked.** An OTA
     window with no address can never receive an upload, so the menu does
     not offer one; WIFI is the lit row, and ABOUT shows the address as a
     dash so the reason is visible.
   - A **second full 3 s hold** while the update window is open still
     switches to the WiFi setup window (hold–hold), unchanged. Any
     release before three seconds still just closes — that escape exists
     because a ~2 s press once left a window stuck.

   The SETTINGS menu itself closes on **any** KEY3 release.
2. **Knowledge** — the upload must carry `Authorization: Bearer <token>`,
   64 lowercase hex from `secrets.h` (`TG_OTA_TOKEN`), never committed.
3. **Time** — the window closes itself after ten minutes; a short KEY3
   press closes it early. While it is closed the HTTP server does not
   even exist in memory (the lazy-surface rule from the 2026-08-14
   freeze lesson).

## What the device verifies before booting anything

- **Metadata gate** on the first kilobyte of the stream: ESP image magic,
  chip = esp32s3, project = "torget", a real app descriptor (its version
  string is shown on the glass during RECEIVING — you see *what* is
  arriving, read from the image itself, never from the uploader's claims).
- **SHA-256** over the whole body must match the `X-VibePulse-SHA256`
  header, or the image is discarded.
- **A/B slots**: the image lands in the *inactive* slot (`ota_0`/`ota_1`,
  5 MB each). Bootloader, partition table, NVS and the running slot are
  never written. USB-C remains the rescue path.
- **Boot-health gate**: on the first boot of a new image
  (`PENDING_VERIFY`), display, UI, scheduler, NVS and memory proofs must
  land within 15 s or the bootloader rolls back to the previous slot on
  the next reset. Only `esp_ota_mark_app_valid_cancel_rollback` blesses
  an image.

## The UPDATE READY notice

The tokenserver reads the version out of the newest `build*/torget.bin`
app descriptor and publishes it as `otaAvailableVersion` on `/api/tokens`
— riding the quota poll the screen already does every 30 s. The screen
compares against its own running version:

- **Mismatch** → full-screen takeover: UPDATE / READY in the ring, the
  waiting version inside, LATER + UPDATE pills below.
- **UPDATE pill** → opens the window; if `tools/ota-flash.sh` is waiting on
  the Mac, delivery is automatic. The pill is the ONLY affirmative action
  while the takeover owns the glass: a KEY3 hold does nothing there, on
  purpose, so SETTINGS can never open behind the notice.
- **LATER / any other tap** → snooze; the takeover returns every hour
  (`TG_NOTICE_NAG_US` in `notice_policy.h` — raise it when the platform
  calms down) until the update is installed.
- **Match** → silence. The notice can never nag about nothing.
- A busy device (open window, running transfer) is never taken over.

Two windows, one port: the OTA window and the WiFi setup window both
serve HTTP on port 80, so only one can exist at a time. If the setup
window needs to open while an OTA window is standing open, it closes the
OTA window first (an OTA window with no network could never receive an
upload anyway) — the log says so. On the glass the OTA overlay always
outranks the network screens: after an OTA reboot with no network, the
re-armed READY ring owns the display and the network-search screen waits
for the window to close.

The nag rhythm lives in `components/torget_ota/notice_policy.c`, a pure
host-tested module (`test/test_ota_notice_policy.c`).

## The sender gates

The device proves an image is *valid*; the pusher proves it is the
*right* one. Four gates run at the moment of upload, all born from the
2026-08-14 ghost incident:

1. **Newest binary at send time** — never a directory picked at script
   start while a build was mid-write.
2. **The embedded version is read from the image and announced** before
   anything is sent.
3. **`-dirty` builds are refused** (`TG_OTA_ALLOW_DIRTY=1` to override).
4. **CI bridge**: the commit in the version string must have a green CI
   run on GitHub (`TG_OTA_ALLOW_NO_CI=1` for offline emergencies). CI
   runs on every pushed branch for exactly this reason.

## Day-to-day developer workflow

```
idf.py build                        # or idf.py -B <dir> build
tools/ota-flash.sh                  # waits for consent, then uploads
```

The device IP is read from a git-ignored `.ota-device` file in the repo
root (write it once: `echo 192.168.1.x > .ota-device`), or pass it as the
first argument. For any agent session starting cold: the repo lives at
`~/Torget` on this machine (the GitHub name is `vibepulse` — the local
directory is not), the OTA work is on the `claude/ota-foundation` branch,
and this file plus `docs/agent-setup.md` are the runbooks.

Hold KEY3 and pick **UPDATE** in SETTINGS (or, if the takeover is already
on the glass, answer it with its UPDATE pill) when you're ready. The hold
alone only reaches the menu — the uploader keeps waiting until you choose
the row. After
the OTA reboot the window re-arms itself once (PENDING_VERIFY boot), so
an iterate-flash-iterate session needs one consent, not one per build —
observed live 2026-08-14: with the pusher armed, a new build delivered
itself straight into the re-armed window with no touch at all. Chained
updates are the intended dev rhythm; a short KEY3 press ends the chain.

Everything the transfer shows on the glass is honest device-owned data:
the ring fills clockwise with the received share, VERIFYING counts the
SHA, the version line names the incoming image.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| "This project has no OTA" | Reading a pre-OTA tree (factory-only `partitions.csv`) | `git branch --show-current`; read `partitions.csv` in *that* checkout |
| Upload gets 403 | Window not open | Hold KEY3, then pick UPDATE in SETTINGS — the hold alone only opens the menu; the glass must show the ring/UPDATES ON |
| Upload gets 401 | Token mismatch or malformed | `TG_OTA_TOKEN` in `secrets.h`: exactly 64 lowercase hex |
| Upload gets 400 "not a torget esp32s3 image" | Wrong file (bootloader? another project?) | Send `build*/torget.bin`, nothing else |
| 202 but the old version still runs after reboot | Health gate rolled the image back | The new build is broken on-device; check it on USB with the console |
| UPDATE READY never appears | Same version already running, or tokenserver older than the feature | `curl localhost:8737/api/tokens \| grep otaAvailable` |
| Takeover shows but UPDATE does nothing | No pusher waiting on the Mac | Start `tools/ota-flash.sh <ip>` — the tap opens the window; the Mac must deliver |
