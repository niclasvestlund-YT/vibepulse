VibePulse v1.2.0 brings board-specific builds to four AMOLED shapes: the original 2.16 square, 2.41 V2 landscape, a 1.91 Touch development port, and a round 1.75 quota profile. Each has its own native layout and installation path. The photographs below show real units; quoted percentages were momentary account readings, not sample values or a claim about today's quota.

> **Verification status:** The release source and native layouts are checked in CI. The new boards have been exercised on physical units to different extents. This tag is not itself a physical flash of every final image. Keep the open checks below with any support claim.

## Three new screens, photographed on glass

### 2.41 V2 · 600 × 450 landscape

The V2 profile has its own display wiring, touch mapping, BOOT settings input and layout. Display bring-up, touch corners, Wi-Fi and visible Codex/Claude usage were checked on a real V2 unit. **V1 is a different board and is not included.** Automatic rotation, OTA and answering prompts on this panel have not been physically verified.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.2.0/docs/img/241-v2/glass-codex.jpg" width="460" alt="Owner photograph of VibePulse Codex usage on a physical Waveshare 2.41 V2"></p>

The [2.41 V2 install and recovery guide](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/waveshare-241-v2.md) covers the exact revision and USB procedure. [Physical evidence and remaining checks](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/superpowers/reviews/2026-09-17-waveshare-241-v2-physical.md).

### 1.91 Touch · 536 × 240 landscape

This is a **USB-installed development port**. One real unit passed four-corner touch mapping, phone Wi-Fi setup, saved-network reconnect and visible Codex usage. Needs You decisions remain on the computer until compact touch targets pass physical review. Menu navigation, OTA and rotation remain unverified.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.2.0/docs/img/191-touch/glass-codex-held.jpg" width="420" alt="Owner photograph of live Codex usage on a physical 1.91 Touch AMOLED"></p>

Use the [1.91 Touch USB guide](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/waveshare-191-touch.md); see its [physical report](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/superpowers/reviews/2026-09-23-waveshare-191-touch.md).

### Round 1.75 · 466 × 466

The round quota page puts weekly usage, today's contribution and a D:H:M reset countdown inside one ring. The unit boots board-specific firmware, joins Wi-Fi and has displayed live Codex usage. The pictured 18% was a momentary reading. Its visible marking is **1.75**; the PCB revision is unknown. Fixed USB-down mounting, touch alignment, the latest two-scan Wi-Fi change and final value accuracy still need an on-unit check. Automatic rotation is unverified. The current profile disables OTA and leaves Needs You decisions on the computer.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.2.0/docs/img/175-round/glass-codex-front.jpg" width="420" alt="Owner photograph of the round 1.75-inch display showing VibePulse Codex weekly usage, used today and reset countdown"></p>

Use the [round 1.75 USB guide](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/waveshare-175-preview.md); see the [physical checkpoint](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/superpowers/reviews/2026-09-24-waveshare-175-round.md).

## Also in this release

Claude Code's statusLine can now supply session and weekly quota readings through an opt-in, single-account macOS bridge. The host also keeps same-window quota floors, reports regressions and sends encrypted live-status updates at most every five seconds. SETTINGS → LABS saves independent choices for optional displays. These changes have source and test evidence, but the earlier v1.0.0 Windows physical-answer and lifecycle result is not being re-claimed for this newer runtime. See the [full changelog](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/CHANGELOG.md) for details.

## Install or upgrade

Check the marking on the actual board, then use its linked guide and explicitly select `TORGET_BOARD=waveshare_241_v2`, `waveshare_191_touch` or `waveshare_175`. The original 2.16 remains the default. **Never flash one board's image to another.** The three new profiles use USB installation; their OTA paths are not verified.

In the checkout used by the computer running the tokenserver:

```sh
git fetch --tags origin
git switch --detach v1.2.0
python3 tools/vibepulse_setup.py status
```

Restart the service to load this code: on macOS run `launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver`; on Windows follow the [stop, wait, then start procedure](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/docs/windows-setup.md#restarting-the-scheduled-task). Then run `python3 tools/tokenserver/smoke.py` to check the running service.

This is a **source-only** release. A local `torget.bin` can contain Wi-Fi credentials and a private device key, so no firmware binary is attached. [Compare v1.1.0...v1.2.0](https://github.com/niclasvestlund-YT/vibepulse/compare/v1.1.0...v1.2.0) · [All supported-screen status and photos](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.2.0/README.md#supported-screens).
