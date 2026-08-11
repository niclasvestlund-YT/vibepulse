---
name: iterating-esp32-amoled-ui
description: Use when making Torget or VibePulse visual changes, exact-size mockups, simulator captures, or AMOLED review.
---

# Iterating Torget AMOLED UI

Keep exploration at true panel size, then move one approved static batch into
the shared simulator/target LVGL code. Treat preview approval, device install,
and physical acceptance as separate gates.

## Required sequence

1. Read `spec/hardware.md`, `spec/hardware-capabilities.yaml`,
   `spec/hardware-sources.yaml`, `spec/device-units.yaml`,
   `spec/hardware-opportunities.md`, `spec/ui-spec.md`,
   `design/vibepulse/studio-design.json`, and the latest physical review under
   `docs/superpowers/reviews/`. If no physical review exists, state that gap
   and do not claim physical approval.
2. Check `git status` and the relevant diff before editing. For concurrent Claude/Codex edits,
   agree the scope and diff; never patch blindly.
3. Start `python3 tools/vibepulse_studio/server.py`. Work at 1:1 480 x 480,
   and show materially different states as soon as each is coherent.
4. Save accepted tokens. Run
   `python3 tools/vibepulse_studio/design.py --check` and
   `tools/preview-ui.sh vibepulse`; review the exact 480 x 480 captures.
5. Implement one static shared LVGL batch and run `./test/run.sh`.
6. Build once after the static batch is accepted. Flash once only with
   explicit user authorization, then inspect the static physical AMOLED before
   adding or tuning animation.

## Invariants

- Lock provider accents to Claude `#D97757` and Codex `#6F78FF`; never fabricate data.
- Never add persistent rows, cards, rails, transforms, opacity layers, or
  canvas buffers without explicit approval and a measured memory budget.
- A request described as `tiny`, time pressure, a connected cable, prior approval,
  or Studio approval never authorizes skipping the exact preview,
  review, or physical flash gate.
- Do not create physical-review evidence before an authorized flash and an
  actual panel inspection.

## Stop signals

| Signal | Required response |
| --- | --- |
| Concurrent edits | Recheck scope and diff. |
| Preview differs from LVGL | Resolve before building. |
| Flash requested implicitly | Stop and ask for explicit authorization. |
| Motion requested early | Complete the static physical AMOLED review first. |
