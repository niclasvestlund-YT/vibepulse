---
name: iterating-esp32-amoled-ui
description: Use when making any Torget AMOLED app visual change, exact-size mockup, simulator capture, or physical review.
---

# Iterating Torget AMOLED UI

Explore at panel size; move an approved batch into shared LVGL.
Preview, install, acceptance are separate gates.

## Required sequence

1. Read `spec/hardware.md`, `spec/hardware-capabilities.yaml`,
   `spec/hardware-sources.yaml`, `spec/device-units.yaml`,
   `spec/hardware-opportunities.md`, `spec/ui-spec.md`,
   `design/vibepulse/studio-design.json`, and the latest physical review under
   `docs/superpowers/reviews/`. If no physical review exists, do not claim approval.
2. Check `git status` and relevant diff. Scope concurrent Claude/Codex edits.
3. Start `python3 tools/vibepulse_studio/server.py`. Work at 1:1 480 x 480;
   show materially different states when coherent.
4. The shared LVGL raster is the visual authority. Test widest realistic copy, a
   missing-data state, broad numbers, and source provenance across
   live, cached/stale, and no-data.
   Compare live/stale with the same active fixture so provenance alone changes.
   Keep one dominant metric; round secondary values; encode discovered spacing as validator tests.
5. Save accepted tokens. Run
   `python3 tools/vibepulse_studio/design.py --check` and
   `tools/preview-ui.sh vibepulse`; review the exact 480 x 480 captures.
6. Implement one static shared LVGL batch and run `./test/run.sh`.
   Use two-stage review: specification fidelity, then code/test quality.
7. Build once after static acceptance. Flash only with explicit user authorization;
   inspect static physical AMOLED before animation.

## interaction performance

Static approval does not imply motion approval. Measure cross-app lag at the
shared display pipeline first.

1. Reproduce the gesture on the physical panel after its static gate.
2. Log gesture begin/end, first redraw, flush count and time, free internal
   heap, largest internal block, and transport errors.
3. Repeat 20 times under network/TLS stress.
4. Change one variable at a time and compare with baseline.

never increase display-buffer height without a measured memory budget and network/TLS
stress run. Prefer fewer deliberate frames when bandwidth is limited.

## Invariants

- Lock provider accents to Claude `#D97757` and Codex `#6F78FF`; never fabricate data.
- Generate image assets at native final sizes; compose before alpha-aware scaling.
  Test transparent corners, decoded palette/pixels, and compare generated files
  byte-for-byte with checked-in firmware assets. Do not scale or recolor at runtime.
- Do not infer visual correctness from a green test. Inspect exact rasters at 1:1;
  assertions must independently prove visible claims, not compare two outputs from
  the same potentially broken renderer.
- Never add persistent UI layers or canvas buffers without approval and a
  measured memory budget.
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
