# LABS selector — static simulator review

This is simulator/build evidence, **not physical-panel approval**. No firmware
was installed during this work. Physical touch, NVS persistence across actual
power cycles, internal heap/largest-block measurements and network/TLS stress
remain required before release or installation.

## Scope

SETTINGS reuses its existing overlay and row controls for two LABS pages.
Four 66 px controls fit at y108, 186, 264 and 342; the footer starts at y442.
There is no new persistent overlay or canvas. Each selected-at-boot feature
creates only its own pages; optional Max Tracker/GitHub tasks also stay off
when their consumers are off. Choices are saved in a separate versioned NVS
namespace and apply after restart.

The fresh sample disables analytics; an old secrets.h lacking that new macro
retains analytics. Existing GitHub macros seed their independent choices.
Saved values override defaults in either direction. Read errors or unknown
record versions do not overwrite the record; failed writes retain the last
successfully selected value and show COULD NOT SAVE.

## Evidence

- Pure C policy checks: all 32 feature combinations, dense tile positions,
  forward/backward traversal, disabled semantic IDs, saved versus active
  choices, reboot, failed writes/reads and unknown records. Initial defaults
  are tested with both analytics seeds and all four GitHub seed combinations.
- Real shared LVGL: all 32 masks, live data, agent/header updates, stale
  transitions and absent pages; menu navigation and a save/revert sequence.
- 59 visual landmark tests pass, including four visible controls and the
  restart footer. 15 documentation-frame checks pass. Exporter checks pass.
- Studio design validation passes. An AddressSanitizer/UndefinedBehaviorSanitizer
  run of the full static simulator capture sequence reported no errors
  (`detect_leaks=0`; this is not leak verification).
- ESP-IDF 5.5.2 single-app build from the sample configuration passes. The
  local resolver selected button 4.2.1 and esp-nn 1.3.2; its unrelated lockfile
  rewrite was discarded. This is compiler evidence with those resolved inputs,
  not validation of the originally locked dependency set or a physical RAM
  measurement. CI must validate the committed checkout before merge.

The initial local full host gate stopped at five existing SessionStart plugin
failures; all five reproduced on the unchanged documentation parent. The
remaining groups were run separately. Tokenserver passes 813/814 tests; the
existing Codex-only provider-gate test still reads a local Claude quota when
it expects null (also reproduced on the parent). These tests were not weakened.
Node is absent locally; the repository's CI owns the JS gates in this run.
Initial build/capture attempts also exhausted local disk space; disposable
captures/downloads from this task were removed and the interrupted checks
were resumed. These environmental failures are not claimed as passing runs.

## Capture boundary

The long object-snapshot sequence intermittently omitted unchanged menu
borders/headings and could affect subsequent captures. Refreshing style caches
was insufficient, and the sanitizer run did not identify a memory violation.
The underlying LVGL snapshot issue is not claimed fixed.

LABS therefore has its own `--vibepulse-labs-captures` process, which never calls
object snapshots: it drives the shared menu callbacks and reads the composed
RGB565 SDL framebuffer through the public display API. Pixel tests assert
both edges of every control and visible heading/footer ink for every mask.
The preview exporter and documentation tests run this mode alongside the
existing page-shell capture mode. RGB565 colors are expanded into RGB BMP/PNG
bytes; the muted border is (148,154,165), the panel-format quantization of
#9298A2. No style-cache workaround was added to the firmware.

Reviewed native frames:

- [Analytics](../../img/vibepulse-labs-analytics.png)
- [GitHub choices](../../img/vibepulse-labs-github.png)
- [Saved change awaiting restart](../../img/vibepulse-labs-pending.png)

Reset clocks, coding quotes and celebrations remain catalogue concepts.
