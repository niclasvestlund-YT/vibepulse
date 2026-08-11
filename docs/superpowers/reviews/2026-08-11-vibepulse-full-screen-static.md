# VibePulse Full-Screen Static Review — 2026-08-11

## Outcome

The five-screen VibePulse redesign passes host, simulator, Studio-contract,
and ESP-IDF build verification. This document does not approve physical
readability or motion. Those gates remain pending until this exact batch is
flashed and photographed on `torget-home-01`.

## Reviewed interface

1. Claude Fable weekly quota
2. Claude all-models weekly quota
3. Codex weekly quota
4. Shared Claude/Codex Burn Rate forecast
5. Claude token Volume

The quota pages use one full-screen anatomy: provider identity, model and
effort, quota name, 164 px percentage, 20 px provider-coloured progress bar,
today's increase, reset distance, and a five-position pager. There is no
persistent VibePulse logo, 5-hour quota, card chrome, or bottom working rail.
Completion remains a temporary overlay.

## Deterministic raster evidence

`tools/preview-ui.sh vibepulse` produced these twelve 480 × 480 captures from
the shared LVGL renderer:

- `vibepulse-claude-fable.png`
- `vibepulse-claude-all.png`
- `vibepulse-codex-weekly.png`
- `vibepulse-burn-speed-up.png`
- `vibepulse-burn-on-pace.png`
- `vibepulse-burn-early.png`
- `vibepulse-burn-learning.png`
- `vibepulse-burn-unavailable.png`
- `vibepulse-volume.png`
- `vibepulse-claude-stale.png`
- `vibepulse-claude-missing.png`
- `vibepulse-codex-missing.png`

The raster checks verify locked Claude `#D97757` and Codex `#6F78FF` fills,
the full-width track, the unboxed Burn Rate separator, empty missing-data
tracks, the exact capture set, and 480 × 480 dimensions.

## Verification evidence

- `PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh`: PASS
- `tools/vibepulse_studio/design.py --check`: covered by the passing host gate
- `test/test_preview_ui.py`: 11 tests PASS
- `test/test_vibepulse_visual_landmarks.py`: 4 tests PASS
- SDL/LVGL simulator build: PASS
- ESP-IDF 5.5.2 target build: PASS
- `torget.bin`: `0x1e6210` bytes; `0x219df0` bytes / 53% of the smallest app partition free
- Linked image size: 1,991,073 bytes
- DIRAM: 261,187 / 341,760 bytes used; 80,573 bytes remain

## Physical and motion gates

| Gate | Status | Required evidence |
|---|---|---|
| Static AMOLED readability | PENDING FLASH / PENDING PHOTO | Arm's-length and 1–2 m inspection of this exact build |
| Touch/swipe responsiveness | PENDING STATIC APPROVAL | 20 swipes per app under active HTTPS/TLS polling |
| Completion overlay | PENDING PHYSICAL CHECK | Done state is obvious, dismissible, and does not obscure quota data afterward |

Do not tune swipe animation or increase `DISPLAY_FLUSH_ROWS` from 12 before
the static gate passes. Motion approval requires gesture, redraw, flush, heap,
largest-block, and transport measurements under real network load.
