# VibePulse Live Quota Polish — Static Review Gate

## Outcome and scope

The original static portion of Tasks 1–5 and the static evidence portion of
Task 7 were implemented through commit `43a21a4`; the quota-source, parser,
native Codex-asset and corrected raster-evidence chain continues through
`3b83b30`. Current focused VibePulse, parser, preview, native-asset and Studio
suites pass. The repository-wide host gate is externally blocked by five root
`README.md` hardware-registry documentation tests outside these scoped commits.

This is not a physical AMOLED approval. Task 6 motion is **not implemented or
enabled**. The usage tick, halo breathing, and attention entry pulses cannot be
implemented or verified until this exact static design has passed an explicitly
authorized install and physical static-panel review.

## Completed static work and commits

| Plan work | Commits recorded in the completed static chain |
|---|---|
| Task 1 — rendered Studio geometry and browser/raster contract | `599a219` `Correct VibePulse rendered hero geometry`; `851ba31` `Align Studio browser geometry contract`; `3d98447` `Lock VibePulse font and raster landmarks` |
| Task 2 — truthful live quota policy | `7b51d71` `Add truthful VibePulse live quota policy`; `27b00a1` `Silence hidden and stale initial quota updates`; `ff2931c` `Cover four active live quota jobs` |
| Static-before-motion gate | `7c32f11` `Gate VibePulse motion on physical static review`; `9f41689` `Place VibePulse physical gate before motion` |
| Task 3 — static Claude/Codex quota pages | `8ff6cc7` `Polish VibePulse Claude and Codex quota pages`; `148f853` `Fix VibePulse quota header state coverage`; `4c2aba6` `Harden VibePulse steady quota state rendering` |
| Task 4 — bounded attention queue | `511af5c` `Queue VibePulse attention events truthfully`; `fbee55a` `Harden VibePulse attention queue defenses`; `3a98bad` `Restart attention pulses on state transitions` |
| Task 5 — static full-screen attention states | `f4cfbfd` `Add VibePulse needs-you attention states`; `e074473` `Support Swedish VibePulse project labels`; `43a21a4` `Cache unchanged VibePulse attention renders` |

Task 4 retains timing phases in policy so a later, gated motion implementation
has authoritative state. The current renderer remains static: repository tests
explicitly reject `lv_anim` in the quota and attention renderers.

## Verification evidence

### 2026-08-12 static fidelity correction

The quota-provenance and native Codex-asset correction adds four explicit,
content-free simulator states: Codex general weekly live at 46%, the same
Codex cycle served from cache as `STALE`, Claude Fable served from cache as
`STALE`, and Claude Fable without a safe value as `NO DATA`. The no-data page
retains the fixed `FABLE · WEEK` identity and dash while leaving progress,
reset and today's delta unavailable. These fixtures construct `tk_tokens`
directly and never query or mutate the user's running tokenserver.

The exact raster gate also checks both source-derived Codex assets independently:
the 32 x 32 quota header and 112 x 112 attention mark have black corners,
substantial blue-gradient and exact-white glyph regions, and a non-rectangular
transparent silhouette. Both render at native 1:1 size.

Latest exact private preview root:

`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.JxtjVs`

The corrected physical AMOLED gate remains pending. No flash or live-service
mutation was performed while producing this evidence.

Observed verification for this correction:

- `tools/vibepulse_studio/design.py --check`: exit 0.
- the current host-gate run passes 11 preview workflow tests, 7 native asset
  tests, 56 Studio tests, 18 Studio wiring tests and 18 exact visual-landmark
  tests before the five unrelated root-document failures described below.
- private preview: 36 allowlisted PNG/BMP pairs, each exactly 480 x 480.
- ESP-IDF 5.5.2 target build: exit 0; `torget.bin` is `0x1d77f0` bytes and
  leaves `0x228810` bytes (54%) of the 4 MiB app partition free.

After the final no-data mask tightening, the focused 18-test visual suite
passes. A fresh complete host-gate run reaches and passes all VibePulse,
preview, native-asset and Studio suites, then fails five hardware-registry
documentation checks because the concurrently committed public landing-page
`README.md` no longer contains the registry's required root-document sections.
That unrelated documentation failure is not recorded as a complete host pass.

The following are completed observations, not expected results:

| Gate | Command/evidence | Observed result |
|---|---|---|
| Studio contract | `PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh` invokes `tools/vibepulse_studio/design.py --check` | PASS; generated layout and Studio design were in sync |
| Historical full host gate (before `3b83b30`) | `PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh` | PASS / exit 0 for the then-current source tree; superseded as current repository-wide evidence |
| Current full host gate at `3b83b30` | `PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh` | BLOCKED after all scoped VibePulse/parser/preview/native-asset/Studio suites pass; five root `README.md` hardware-registry documentation tests fail outside this chain |
| Private preview | `PYTHON_BIN="$PWD/.venv/bin/python" ./tools/preview-ui.sh vibepulse` | PASS / exit 0; exact allowlisted matrix generated privately by the shared SDL/LVGL renderer |
| Current static visual landmarks | `test/test_vibepulse_visual_landmarks.py` | 18 tests PASS; complete 36-capture matrix, every image 480 × 480 |
| Target build | ESP-IDF 5.5.2 `idf.py build` | PASS / exit 0; `/Users/niclasvestlund/Torget/build/torget.bin` is `0x1d77f0` bytes; the 4 MiB app partition has `0x228810` bytes, 54%, free |

The target build compiled `agent_completion_policy.c` and `agent_monitor.c`
into `app_tokens` and linked `torget.elf` before producing `torget.bin`.
`components/app_tokens/CMakeLists.txt` registers the static quota policies and
renderer for target; `sim/CMakeLists.txt` compiles the same sources for the
pixel-authoritative simulator.

## Exact private capture matrix

The current corrected matrix is rooted at:

`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.JxtjVs`

The 32-image `vibepulse-preview.xMSkn4` listing below is retained as historical
pre-correction evidence and is superseded by the 36-image matrix above.

Exact 1:1 PNG paths:

```text
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-burn-early.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-burn-learning.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-burn-on-pace.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-burn-speed-up.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-burn-unavailable.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-all.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-done-static.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-error.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-fable.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-idle.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-lease-expired.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-missing.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-multi-chat.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-needs-you.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-single-working.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-stale.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-swedish-project.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-today-contradictory.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-today-missing.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-claude-zero-total.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-done-static.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-error.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-full-total.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-idle.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-missing.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-multi-chat.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-needs-you.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-single-working.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-stale.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-codex-weekly.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-two-waiting-queued.png
/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/vibepulse-volume.png
```

The corresponding authoritative BMPs are in the exact directory
`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4/captures`,
with the same state basename prefixed by `torget-`.

Earlier focused attention evidence was generated at
`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.TrGFiZ`.
The Swedish UTF-8 follow-up was generated at
`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.Csjfxo`;
the manually inspected exact-size image was
`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.Csjfxo/vibepulse-claude-swedish-project.png`.

## Static raster and 1:1 inspection

The automated landmarks verify the exact 32-image allowlist and 480 × 480
dimensions; locked Claude/Codex segmented-bar colors; three-pixel markers and
0/100 endpoints; missing, stale, lease-expired, and contradictory-data truth;
static provider-colored working halos; six-pixel attention outlines; real
provider assets; reviewed copy rows; distinct waiting/error/count states; and
uppercase Swedish project glyph bounds.

The native-size Swedish attention image was also inspected at 1:1 and showed
`RÄKSMÖRGÅS` without tofu, truncation, or row overflow. The Task 5 static
attention capture set was reviewed separately before the UTF-8 and renderer
cache follow-ups. These simulator observations do not make a claim about
brightness, viewing distance, touch behavior, or appearance on physical glass.

## Separate review outcomes

- Spec review found one blocking mismatch: project input accepted valid UTF-8
  while uppercasing and the 25 px font were ASCII-only. Commit `e074473`
  defined the display-safe alphabet, added deterministic Swedish uppercasing
  and pinned glyph generation, and added a non-ASCII raster regression. Its
  focused tests, the then-current full host gate, private preview, and target
  build passed at that historical point.
- Quality review found one Important blocker: the 100 ms monitor tick repeated
  unchanged label/style writes for persistent overlays. Commit `43a21a4`
  added a render key covering visibility, event generation, provider, state,
  project, and count/detail inputs. Focused regression tests prove unchanged
  WAITING/ERROR skips rendering while the policy tick still advances DONE to
  the next queued event. Its focused tests, the then-current full host gate,
  private preview, and target build passed at that historical point.

No review outcome authorizes physical installation or Task 6 motion.

## Delivery facts and unresolved physical gate

Read-only repository checks show:

- `sdkconfig` selects custom `partitions.csv`.
- `partitions.csv` contains one `factory, app, factory, 4M` application
  partition and no `ota_0`, `ota_1`, or `otadata` entries.
- `components/torget_ota` is absent.

Therefore this static build is factory-only and cannot be delivered by OTA.
Installing it requires USB and explicit user authorization for the exact
device write. No flash was performed for this static review package, and this
document makes no physical-device success claim.

Pending before Task 6 may start:

1. Obtain explicit authorization for the USB flash and resolve the exact
   serial target.
2. Install this exact static build and confirm boot, VibePulse data, and that
   Solelkollen and Vibbe still launch.
3. Inspect Claude/Codex quota pages, static halos, needs-you/error/DONE states,
   long Swedish project text, stale/missing states, and provider colors on the
   physical AMOLED at normal desk and distance viewing.
4. Verify real touch dismissal, long-press launcher behavior, and swipe
   responsiveness without treating simulator input as physical evidence.
5. Record photographs and pass/fail observations. Only after the physical
   static gate passes may Task 6 motion be implemented, enabled, or verified.

## Working-tree boundary

`design/vibepulse/exports/claude-hero.png` is a pre-existing user-owned dirty
file. It was explicitly left untouched and is not part of this evidence
document or its commit.
