---
name: iterating-esp32-amoled-ui
description: Use when making any Torget AMOLED app visual change, exact-size mockup, simulator capture, or physical review.
---

# Iterating Torget AMOLED UI

Explore at true panel size, then move one approved static batch into shared
simulator/target LVGL. Preview, device install, and physical acceptance are
separate gates.

## Required sequence

1. Read `spec/hardware.md`, `spec/hardware-capabilities.yaml`,
   `spec/hardware-sources.yaml`, `spec/device-units.yaml`,
   `spec/hardware-opportunities.md`, `spec/ui-spec.md`,
   `design/vibepulse/studio-design.json`, and the latest physical review under
   `docs/superpowers/reviews/`. If no physical review exists, state that gap
   and do not claim physical approval.
2. Check `git status` and the relevant diff. For concurrent Claude/Codex edits,
   agree the scope and diff.
3. Start `python3 tools/vibepulse_studio/server.py`. Work at 1:1 480 x 480,
   and show materially different states as soon as each is coherent.
4. The shared LVGL raster is the visual authority. Test the widest realistic copy,
   a missing-data state, and broad numbers. Keep one dominant metric;
   round secondary values when precision crowds it. When the raster finds an
   overlap, encode discovered spacing as validator tests.
5. Save accepted tokens. Run
   `python3 tools/vibepulse_studio/design.py --check` and
   `tools/preview-ui.sh vibepulse`; review the exact 480 x 480 captures.
6. Implement one static shared LVGL batch and run `./test/run.sh`.
7. Build once after the static batch is accepted. Flash once only with
   explicit user authorization, then inspect the static physical AMOLED before
   adding or tuning animation.

## Interaction performance

Static approval does not imply motion approval. For cross-app lag, measure
interaction performance at the shared display pipeline first.

1. Reproduce the exact gesture on the physical panel after its static gate.
2. Log gesture begin/end, first redraw, flush count and time, free internal
   heap, largest internal block, and transport errors.
3. Repeat at least 20 times during real network/TLS stress.
4. Change one variable at a time and compare against the recorded baseline.

Keep verified bus and memory settings: never increase display-buffer height
without a measured memory budget and another network/TLS stress run. Prefer
fewer deliberate frames when bandwidth is the limit.

## Invariants

- Lock provider accents to Claude `#D97757` and Codex `#6F78FF`; never fabricate data.
- Never add persistent rows, cards, rails, transforms, opacity layers, or
  canvas buffers without explicit approval and a measured memory budget.
- `tiny`, time pressure, a connected cable, prior approval, or Studio approval
  never authorizes skipping preview, review, or the physical flash gate.
- Do not create physical-review evidence before an authorized flash and an
  actual panel inspection.

## Stop signals

| Signal | Required response |
| --- | --- |
| Concurrent edits | Recheck scope and diff. |
| Preview differs from LVGL | Resolve before building. |
| Flash requested implicitly | Stop and ask for explicit authorization. |
| Motion requested early | Complete the static physical AMOLED review first. |
| Motion feels slow across apps | Instrument the shared pipeline before changing UI code. |
