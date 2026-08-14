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
│                          │          │   · hold KEY3 ~3 s          │
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
   device itself: a ~3 s KEY3 hold, or a tap on the UPDATE pill when the
   UPDATE READY takeover is showing. A script, a LAN neighbour, or a
   compromised Mac cannot open it remotely. On the takeover, the UPDATE
   pill is the *only* yes — a tap anywhere else (including LATER)
   snoozes, so an accidental touch always lands on the safe side.
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
- **UPDATE pill** (or a KEY3 hold) → opens the window; if
  `tools/ota-flash.sh` is waiting on the Mac, delivery is automatic.
- **LATER / any other tap** → snooze; the takeover returns every hour
  (`TG_NOTICE_NAG_US` in `notice_policy.h` — raise it when the platform
  calms down) until the update is installed.
- **Match** → silence. The notice can never nag about nothing.
- A busy device (open window, running transfer) is never taken over.

The nag rhythm lives in `components/torget_ota/notice_policy.c`, a pure
host-tested module (`test/test_ota_notice_policy.c`).

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

Hold KEY3 (or answer the takeover with UPDATE) when you're ready. After
the OTA reboot the window is designed to re-arm itself once
(PENDING_VERIFY boot), so an iterate-flash-iterate session needs one
consent, not one per build.

Everything the transfer shows on the glass is honest device-owned data:
the ring fills clockwise with the received share, VERIFYING counts the
SHA, the version line names the incoming image.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| "This project has no OTA" | Reading a pre-OTA tree (factory-only `partitions.csv`) | `git branch --show-current`; read `partitions.csv` in *that* checkout |
| Upload gets 403 | Window not open | Hold KEY3; the glass must show the ring/UPDATES ON |
| Upload gets 401 | Token mismatch or malformed | `TG_OTA_TOKEN` in `secrets.h`: exactly 64 lowercase hex |
| Upload gets 400 "not a torget esp32s3 image" | Wrong file (bootloader? another project?) | Send `build*/torget.bin`, nothing else |
| 202 but the old version still runs after reboot | Health gate rolled the image back | The new build is broken on-device; check it on USB with the console |
| UPDATE READY never appears | Same version already running, or tokenserver older than the feature | `curl localhost:8737/api/tokens \| grep otaAvailable` |
| Takeover shows but UPDATE does nothing | No pusher waiting on the Mac | Start `tools/ota-flash.sh <ip>` — the tap opens the window; the Mac must deliver |
