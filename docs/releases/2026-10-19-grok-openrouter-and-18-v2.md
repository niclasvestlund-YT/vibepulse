VibePulse v1.4.0 adds an optional Grok page, brings OpenRouter spend into Labs,
and packages the 1.8 V2 development profile. All new integrations are off by
default and require their own host and firmware configuration.

## Grok plan observations and bot activity

The **GROK** preview uses the familiar quota layout on all five board profiles.
Subscription observations come from a local `grok.json`: a copied percentage,
a user count against a chosen limit, or an estimate from completed bot tasks.
Estimates carry **EST**; copied readings carry their age and become cached.
These are not live subscription readings. Optional xAI postpaid invoice data
and OpenRouter spend are separate billing sources.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.4.0/docs/img/grok-216-plan.png" width="480" height="480" alt="Native 480 by 480 Grok simulator page with an estimated plan percentage and sample bot activity"></p>

This is native shared LVGL output with synthetic data. The local status helper
can report WORKING, NEEDS YOU, DONE, IDLE or ERROR; the Grok page shows status
and cannot approve actions. Bot reports are accepted only on loopback, and the
Grok feed is never sent through VibePulse relays.

Enable `--grok` on the existing tokenserver and `-DTORGET_GROK=ON` in the
selected board build. Grok is a build option, not a Labs switch.
[Setup, source meanings and limits](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.4.0/docs/grok-pulse.md).

## OpenRouter in Labs

OpenPulse adds the optional **OPENROUTER** Labs page on round 1.75 and square
2.16. A separate macOS service reads API keys from Keychain and sends summaries
over the LAN. See spend against a monthly display budget, key allowance and
optional account credits. The display budget is a reminder, not a provider cap.
Existing Labs choices are preserved on upgrade.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.4.0/docs/img/openpulse/round-spend.png" width="466" height="466" alt="Native 466 by 466 OpenRouter simulator spend page with synthetic demo values"></p>

The image uses synthetic values. An owner checkpoint records live readings,
upright output and the bottom page switch on one round unit. Full touch-grid,
swipe/menu and stress acceptance remain open; square 2.16 has simulator/build
evidence, and the other three profiles cannot enable OpenPulse yet.
The service has no automatic startup or relay, and Windows support is not
claimed. [Installation and physical evidence](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.4.0/docs/openpulse/README.md).

## Five board profiles, including 1.8 V2

The release includes the USB-only **Waveshare 1.8 V2** development profile at
368 × 448, alongside 2.16, 2.41 V2, 1.91 Touch and round 1.75.
The owner reports display, touch, Wi-Fi/data and settings working on one 1.8 V2
unit. V1 is incompatible. Four-corner touch, transform-memory stress, OTA,
Windows and unused peripherals remain unverified for this profile.
[Identify the board and follow its USB guide](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.4.0/docs/waveshare-amoled-18-v2.md).

## Verification and upgrade

The Grok implementation passed the required GitHub checks: the complete host
gate, tokenserver tests on Linux/macOS/Windows, both relay suites, snapshot
tests on Linux/macOS and ESP-IDF firmware builds. CI builds every board with
Grok off and on. Shared LVGL regressions cover missing, retained, expired and
restored readings, currency precision/glyphs and bot counts. A separate local
Windows review run passed 272 Grok and HTTP tests, with three platform-specific
skips.

Grok has **no physical panel acceptance**. Real xAI/OpenRouter credentials and
Grok host-service operation were not validated in this release review. Existing
Claude/Codex and board checkpoints remain tied to their recorded revisions;
automated tests do not renew physical or real-host claims.

Fetch the release source, preserve your existing `secrets.h` and service options,
and follow the guide for your exact board. Restart tokenserver after updating
its code. New firmware features require a board-specific build and a separately
authorized installation. This source release does not flash a display.
No firmware binary is attached: `torget.bin` contains private Wi-Fi/device data.

[Full changelog](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.4.0/CHANGELOG.md)
· [Compare v1.3.0...v1.4.0](https://github.com/niclasvestlund-YT/vibepulse/compare/v1.3.0...v1.4.0)
