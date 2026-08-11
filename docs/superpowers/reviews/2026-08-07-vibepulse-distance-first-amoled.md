# VibePulse distance-first physical AMOLED review

**Status:** DRAFT — **PENDING PHOTO**

**Evidence recorded:** 2026-08-08. This document records deterministic
simulator images and target-build evidence only. It is not a physical-panel
approval: no current panel photographs or distance observations are attached.

## Claude hero at 1–2 m

**PENDING PHOTO.** The current native 480 × 480 simulator capture
`/tmp/torget-vibepulse-claude-hero.bmp` shows the `V.` app mark and `CLAUDE`,
a 73% hero value, no card behind the value, a fully visible 18 px bar, and
fully visible reset copy. Source layout uses `plex_num_146` for the hero,
21 px-equivalent critical copy, and `HERO_BAR_H 18`.

## Codex hero at 1–2 m

**PENDING PHOTO.** The current native 480 × 480 simulator capture
`/tmp/torget-vibepulse-codex-hero.bmp` shows `CODEX`, a 35% hero value, no
card behind the value, a fully visible 18 px bar, and fully visible reset
copy. Source layout uses `plex_num_146` and 21 px-equivalent critical copy.

## Claude details at arm's length

**PENDING PHOTO.** The capture
`/tmp/torget-vibepulse-claude-details.bmp` contains the real Claude 32 px
asset, both Claude limits, and fully visible percentage, bar, and reset
content. Summary bars are 16 px high; the displayed labels/reset copy are
21 px-equivalent.

## Dual-provider overview at arm's length

**PENDING PHOTO.** The capture `/tmp/torget-vibepulse-overview.bmp` contains
the real Claude 32 px asset and the composed three-layer Codex 32 px asset.
Both providers, values, bars, and reset copy are visible with no clipping.
Missing/stale captures were also generated for both hero pages:
`claude-hero-stale`, `codex-hero-stale`, `claude-hero-missing`, and
`codex-hero-missing`. Stale state only dims the provider header; it retains
the last accepted quota. Missing state shows `–` and `USAGE UNAVAILABLE`.
The screen code contains no timer-driven view change; `usage_screen_show_view`
uses `LV_ANIM_OFF` and changes pages only when called manually.

## Day and night brightness

**PENDING PHOTO.** No current day/night panel photographs or direct panel
observations are available.

## Required corrections before motion

No simulator or target-build defect warrants a UI change. The physical gate
remains open until current photographs/observations establish readable Claude
and Codex heroes at 1–2 m, readable detail and overview pages at arm's length,
and acceptable day/night brightness. Do not enable motion or automatic content
changes before that evidence is recorded.

## Technical evidence (not physical approval)

- Static-capture gate: **PASS** for provider identity (Claude/Codex wordmarks;
  real Claude/Codex assets on the two summary pages), percent dominance,
  21 px minimum critical copy, 18 px hero and 16 px summary bars, no card
  behind a hero percentage, no clipping/tofu, and no automatic content swap.
  This is simulator/source evidence, not an assessment of physical readability.
- `./sim/build/torget-sim --vibepulse-static-qa` regenerated all four normal
  480 × 480 BMPs plus both stale and both missing hero variants on 2026-08-08.
  Native-size visual inspection found no tofu glyphs or clipping.
- ESP-IDF 5.5 target build produced `build/torget.bin` (1,882,256 bytes). The
  image is 0x1cb890 bytes in the 0x400000-byte smallest app partition, leaving
  0x234770 bytes (55%). DIRAM is 76.47% used with 80,413 bytes remaining;
  IRAM is fully allocated (16,384 bytes) but the linker completed. The target
  build includes `plex_num_146`, `plex_num_118`, `plex_text_21`, and
  `plex_text_16` font sources.
- USB inventory found an Espressif device (VID 0x303a), but no
  `/dev/cu.usbmodem*` bootloader port. `./tools/flasha.sh` was run exactly and
  waited for that port; no flash write occurred. Consequently, no esptool hash
  verification or reset evidence exists.
