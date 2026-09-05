Read `README.md` for the repository structure and build workflow.

Setting this repo up for someone (secrets, build, flash, tokenserver)? Follow
`docs/agent-setup.md` — step-by-step, with verifications and a symptom→fix
table. Never flash the board without the user explicitly asking you to.

## Over-the-air updates

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
after editing `tools/tokenserver/`, restart the launchd service
(`launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver`) — the running
process keeps old code and the panel honestly shows the gap.

## AMOLED visual work

Use `.claude/skills/iterating-esp32-amoled-ui/SKILL.md` for AMOLED work. Show
exact 480 x 480 output at meaningful stages. Review the static physical AMOLED
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

Before proposing external hardware, declaring a device limitation, or designing
a hardware-dependent feature, read `spec/hardware.md`,
`spec/hardware-capabilities.yaml`, `spec/hardware-sources.yaml`,
`spec/device-units.yaml`, and `spec/hardware-opportunities.md`. State whether
the idea is only silicon-capable, board-wired, firmware-enabled, and
physically verified on the named unit. Mention a relevant
unused onboard capability when it materially improves the request.
Never copy secrets or turn an opportunity into authorized implementation work.

## Releases and the README

Two rules, learned 2026-08-16, not optional:

- **When an important feature ships, update `README.md` in the same effort** —
  headline it at the top (tagline + intro) AND add/refresh its own section with
  current 480 x 480 simulator frames. A feature nobody can see in the README is
  a feature nobody adopts.
- **Every GitHub release gets real feature images and a clean card.** Embed the
  feature's simulator frames in the release body via absolute
  `raw.githubusercontent.com/.../<tag>/...` URLs. Do NOT open the body with a
  `# Release: ...` H1 — GitHub renders it huge and it collides with the title in
  the auto-generated OG card; lead with a plain intro paragraph instead.
  Releases are source-only — never attach `torget.bin` (WiFi creds + device key
  compiled in). The per-release OG card is auto-generated and NOT customisable;
  the one shared-link image you can set is the repo's Social preview (Settings ->
  Social preview) — refresh it for a major feature.
- **Tagging includes the documentation cut.** In the same effort, move the
  shipped entries out of `Unreleased` into a dated version in `CHANGELOG.md`,
  leave a new empty `Unreleased` section at the top, update README's `Latest
  release`, and save the GitHub-ready body under `docs/releases/`. A tag on a
  commit whose changelog still calls its features unreleased is not a finished
  release.
