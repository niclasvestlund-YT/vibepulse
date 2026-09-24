Read `README.md` for the repository structure and build workflow.

Follow `CONTRIBUTING.md` and `docs/maintaining-contributions.md` for contributions
and merging. Maintainer changes also go through a PR with passing required
checks. Documented local test omissions do not waive GitHub's merge requirements.

Setting this repo up for someone (secrets, build, flash, tokenserver)? Follow
`docs/agent-setup.md` — step-by-step, with verifications and a symptom→fix
table. Never flash the board without the user explicitly asking you to.

## Over-the-air updates

This workflow applies to the original 2.16 board. Update 2.41 V2, 1.91 Touch and 1.75
over USB using their board-specific guides; OTA and board identification in the
update chain are not validated for these ports.

Day-to-day firmware goes over the air: `idf.py build && tools/ota-flash.sh`
(device IP from git-ignored `.ota-device`). The full loop, consent model and
troubleshooting live in `docs/ota.md` — read it before touching anything
OTA. Non-negotiables: the maintenance window opens ONLY from the device (a
3 s KEY3 hold opens SETTINGS, where UPDATE opens it — greyed out without an
address, since a window with no address cannot receive an upload; WIFI opens
the setup window, see `docs/wifi.md` — or the UPDATE pill on the takeover) —
never claim or imply a
script can; the sender gates (newest-binary-at-send, version printed,
-dirty refused) exist because a stale archived build once froze the panel —
never bypass them with TG_OTA_ALLOW_DIRTY without the user saying so; and
after editing `tools/tokenserver/`, restart the host service
(macOS: `launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver`;
Windows has no launchd — the service runs under Task Scheduler, so restart it
with `Stop-ScheduledTask`, a wait until it has actually stopped, and only then
`Start-ScheduledTask` on the task `VibePulse tokenserver`; the task is
registered `-MultipleInstances IgnoreNew`, so an immediate start is silently
discarded and the service stays down until the five-minute watchdog — the
snippet is under "Restarting the scheduled task" in `docs/windows-setup.md`.
Never restart it by rerunning `install-windows-task.ps1`, which
rebuilds the task's command line from that invocation's arguments and so
silently drops the `-PublishUrl`, `-GithubRepo` and plan/cost settings it was
installed with) — the running process keeps old code and the panel honestly
shows the gap.

## AMOLED visual work

Use `.claude/skills/iterating-esp32-amoled-ui/SKILL.md` for AMOLED work. Show
exact native output at meaningful stages (2.16: 480 x 480; 2.41 V2: 600 x 450). Review the static physical AMOLED
before motion. Studio approval never authorizes a flash; obtain explicit user
authorization for the physical install.

## Logs, errors, and learning from mistakes

`docs/observability.md` maps every log the system generates and contains the
periodic comb routine — follow it when asked to comb, audit, or investigate
logs or odd behavior. Findings go to `docs/observability-backlog.md`. Read
`docs/lessons.md` before touching pollers, parsers, staleness logic, or the
host-service setup: most sharp edges here have a story, and fixes with a
root-cause story add an entry there.

## Hardware-aware work

Before each hardware build/install, resolve the intended physical unit from its
board registry and the current session. The unit used in the 2026-09-24 Lovable
session was `vibepulse-175-01`: round 1.75, `waveshare_175`, 466 × 466, USB down,
BOOT held three seconds for Settings. This is a recorded unit, not a permanent
assumption about whichever screen is connected next. A USB port name or the
ESP32-S3 chip type alone cannot identify the panel. Bind the connected ROM
identity to the intended unit using the board's installation workflow; clarify
only if the available evidence cannot resolve the model.

Inspect the effective CMake board and companion inputs before building. For a
VibePulse-only install, explicitly exclude local Solelkollen/Buddy checkouts;
do not let auto-discovery silently change the app registry. Review native-size
output, pair display and touch rotation, and keep build, flash/hash verification,
visual inspection and touch acceptance as separate evidence.

Select the board first. The five root files below describe **2.16 only**.
For the experimental **1.91 Touch AMOLED**, use the five files under
`spec/boards/waveshare_191_touch/` and `docs/waveshare-191-touch.md`.
Native geometry is 536 × 240; USB updates only. Keep its open physical
verification items explicit.
For **2.41 V2**, read the same five filenames under
`spec/boards/waveshare_241_v2/`; validate that directory separately. Never
transfer installed firmware or physical verification between board registries.
For the round **1.75**, read the five files under
`spec/boards/waveshare_175/` and `docs/waveshare-175-preview.md`.
PCB revision remains unknown; quota display is photographed on one unit,
while touch decisions, OTA and automatic rotation remain unverified.

Before proposing external hardware, declaring a device limitation, or designing
a hardware-dependent feature, read `spec/hardware.md`,
`spec/hardware-capabilities.yaml`, `spec/hardware-sources.yaml`,
`spec/device-units.yaml`, and `spec/hardware-opportunities.md`. State whether
the idea is only silicon-capable, board-wired, firmware-enabled, and
physically verified on the named unit. Mention a relevant
unused onboard capability when it materially improves the request.
Never copy secrets or turn an opportunity into authorized implementation work.
