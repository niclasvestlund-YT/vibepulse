# VibePulse Live Quota Polish — Static Review Gate

## Outcome and scope

The static portion of Tasks 1–5 and the static evidence portion of Task 7 are
implemented through commit `43a21a4`. Studio validation, the complete host
gate, the private exact-size preview, static raster landmarks, and the ESP-IDF
target build passed for the current source tree.

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

The following are completed observations, not expected results:

| Gate | Command/evidence | Observed result |
|---|---|---|
| Studio contract | `PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh` invokes `tools/vibepulse_studio/design.py --check` | PASS; generated layout and Studio design were in sync |
| Full host gate | `PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh` | PASS / exit 0, including pure C policy tests, 11 preview tests, 56 Studio design/server tests, 18 Studio wiring tests, and 16 static visual-landmark tests |
| Private preview | `PYTHON_BIN="$PWD/.venv/bin/python" ./tools/preview-ui.sh vibepulse` | PASS / exit 0; exact allowlisted matrix generated privately by the shared SDL/LVGL renderer |
| Static visual landmarks | `test/test_vibepulse_visual_landmarks.py`, as part of the full host gate | 16 tests PASS; complete 32-capture matrix, every image 480 × 480 |
| Target build | ESP-IDF 5.5.2 `idf.py build` | PASS / exit 0; `/Users/niclasvestlund/Torget/build/torget.bin` is `0x1e9d00` bytes (2,006,272 bytes); the 4 MiB app partition has `0x216300` bytes (2,188,032 bytes), 52%, free |

The target build compiled `agent_completion_policy.c` and `agent_monitor.c`
into `app_tokens` and linked `torget.elf` before producing `torget.bin`.
`components/app_tokens/CMakeLists.txt` registers the static quota policies and
renderer for target; `sim/CMakeLists.txt` compiles the same sources for the
pixel-authoritative simulator.

## Exact private capture matrix

The latest verified matrix is rooted at:

`/var/folders/86/2jzw_gkj3c71ygn1hdvrsnx80000gn/T/vibepulse-preview.xMSkn4`

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
  focused tests, full host gate, private preview, and target build passed.
- Quality review found one Important blocker: the 100 ms monitor tick repeated
  unchanged label/style writes for persistent overlays. Commit `43a21a4`
  added a render key covering visibility, event generation, provider, state,
  project, and count/detail inputs. Focused regression tests prove unchanged
  WAITING/ERROR skips rendering while the policy tick still advances DONE to
  the next queued event. Its focused tests, full host gate, private preview,
  and target build passed.

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
