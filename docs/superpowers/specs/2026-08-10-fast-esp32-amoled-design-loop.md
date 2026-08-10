# Fast ESP32 AMOLED Design Loop

## Outcome

Make small-display design changes visible in minutes without rebuilding and
flashing firmware for every copy, spacing, hierarchy, or color adjustment.
Codex and Claude Code must use the same checked-in workflow.

## Shared skill

Keep the canonical skill at
`.claude/skills/iterating-esp32-amoled-ui/` so Claude Code discovers it from
the Torget project. Link Codex's personal skill directory to that same folder;
do not maintain two copies.

The skill applies when designing or revising an ESP32/LVGL UI where the user
wants faster visual iteration, exact-size mockups, fewer flashes, or staged
feedback.

## Fast loop

1. Inspect the current 480×480 screen, its physical photo, and the relevant
   design rules once.
2. Explore layout, hierarchy, color, and copy in a disposable exact-size
   visual mockup. Do not edit the shared LVGL source during exploration.
3. Render exact 480×480 PNGs and show the user the important states as soon as
   each is coherent. Do not wait for the entire redesign.
4. Implement the approved direction in the shared simulator/target LVGL code.
5. Run host tests and an incremental target build once per approved batch.
6. Flash once, inspect the static screen on the physical AMOLED, then add or
   tune animation. Never postpone the physical check until after animation.
7. Reflash only for target-only behavior, hardware interaction, or the final
   accepted visual batch.

## Torget tooling

Add `tools/preview-ui.sh vibepulse`. It configures the simulator only when
needed, performs an incremental Ninja build, runs the existing VibePulse
static-QA capture, converts its framebuffer BMPs to 480×480 PNGs in a fresh
temporary directory, and prints their paths.

Add a concise fast-loop section to `AGENTS.md`. It must define the preview
command, checkpoint rules, the static-AMOLED-before-animation gate, and these
safety constraints:

- no fabricated data;
- no large LVGL transforms, layered opacity, or canvas buffers without a
  measured memory budget;
- no new persistent rows, cards, screens, or status rails without explicit
  approval;
- preserve provider colors: Claude `#D97757`, Codex `#6F78FF`;
- show output between meaningful stages instead of presenting a finished
  redesign all at once.

## Validation

Add a host wiring test that initially fails while the shared skill, preview
script, and project rules are absent. After implementation:

- run the wiring test and `quick_validate.py` for the skill;
- run `tools/preview-ui.sh vibepulse` and verify every output image is exactly
  480×480;
- run `./test/run.sh`;
- do not flash as part of installing this workflow.

The currently running diagnostic firmware remains untouched until the new
VibePulse visual direction is approved.
