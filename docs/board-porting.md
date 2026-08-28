# Porting VibePulse to more AMOLED boards

Investigation and work plan for taking VibePulse off its single pinned panel
(Waveshare ESP32-S3-Touch-AMOLED-2.16, 480 × 480) and onto four more boards.
Written 2026-08-28, before any of the new hardware was in hand, so that the
week of waiting is spent on the ~80 % of the port that needs no board.

Companion to [issue #5](https://github.com/niclasvestlund-YT/vibepulse/issues/5),
which already frames the port surface. This document adds the per-board facts,
the file-level audit, and an ordered plan.

> **Evidence discipline.** Every fact below is tagged with how it was
> established. `[bsp]` = read out of Waveshare's own BSP source
> (`waveshareteam/Waveshare-ESP32-components`, cloned 2026-08-28). `[hw-ref]` =
> Waveshare's published hardware reference for that board. `[web]` = vendor
> product/wiki page via search, not cross-checked against source. `[unknown]` =
> not established. Nothing here is `unit_verified`; no physical panel has been
> touched. Do not promote anything to verified without a real unit, per
> `spec/hardware.md`.

---

## 1. The boards

| Board | SKU | Panel | Driver | Touch | LVGL glue | Upstream BSP | Evidence |
|---|---|---|---|---|---|---|---|
| **2.16** (reference, shipping) | — | 480 × 480 square | CO5300, **big-endian** RGB565 | CST9217 | `esp_lvgl_adapter ~0.6` | `waveshare/esp32_s3_touch_amoled_2_16` 2.0.1 | `[bsp]` + unit-verified |
| **1.75** | 31261 | **466 × 466 round** | CO5300, **little-endian** | CST9217 | `esp_lvgl_adapter ~0.6` | `waveshare/esp32_s3_touch_amoled_1_75` **3.0.1** | `[bsp]` `[hw-ref]` |
| **1.8** | 29957 | **368 × 448 portrait** | CO5300 (rev V2) / SH8601 (rev V1) | CST816S **or** FT5x06, probed at runtime (V1: FT3168) | **`esp_lvgl_port ^2`** | `waveshare/esp32_s3_touch_amoled_1_8` 2.0.3 | `[bsp]` `[web]` |
| **2.41** | 30563 | **600 × 450 landscape** | **RM690B0** | **FT6336** | — | **none** | `[web]` |
| **"1.9"** | 28596 | **unconfirmed** | — | — | — | **none** | `[unknown]` |

### The 1.9 is not identified yet

There is no Waveshare product called *ESP32-S3-Touch-AMOLED-1.9*. SKU 28596
most plausibly resolves to **ESP32-S3-Touch-AMOLED-1.91** — 240 × 536,
RM67162 over QSPI, FT3168 touch, QMI8658 IMU `[web]`. The other candidate is
ESP32-S3-LCD-1.9 (170 × 320 IPS, not AMOLED, touch optional). **Confirm the
SKU before planning around it**: 240 × 536 is a 2.23:1 tall strip, which is
not a rescale of any existing VibePulse screen — it is a different information
design. Everything in this document treats the 1.9 as "unknown board, assume
the 1.91 strip until the box says otherwise".

### Verified per-board deltas (read out of BSP source)

| | 2.16 | 1.75 | 1.8 |
|---|---|---|---|
| `BSP_LCD_H_RES × V_RES` | 480 × 480 | 466 × 466 | 368 × 448 |
| `BSP_LCD_BIGENDIAN` | **1** | **0** | **0** |
| Init `MADCTL 0x36` | `0xA0` (panel mounted rotated) | **absent** (default 0x00) | **absent** |
| Init `CASET` | `0x0000..0x01DF` | `0x0006..0x01D7` | `0x0000..0x016F` |
| Column gap | not set at init; we set it per rotation | **`set_gap(6, 0)` at init** | runtime `bsp_display_set_x_gap()` |
| Touch flags in BSP demo | `swap_xy=1, mirror_x=0, mirror_y=1` | **`swap_xy=0, mirror_x=1, mirror_y=1`** | `swap_xy=0, mirror_x=0, mirror_y=0` |
| `bsp_display_lock` signature | `bool` (the inverted-truth bug, `spec/hardware.md`) | **`esp_err_t`** (bug fixed upstream) | `bool` → `lvgl_port_lock` |
| Draw buffers | partial double, `buffer_height 50`, PSRAM | partial double, `buffer_height 50`, PSRAM | **single** buffer, size from Kconfig bounce height |
| LCD reset | GPIO39 | GPIO39 | **`GPIO_NUM_NC`** (via TCA9554) |
| LCD PCLK | GPIO38 | GPIO38 | **GPIO11** |
| Touch INT / RST | 11 / 40 | 11 / 40 | **21 / NC** |
| I2S MCLK | GPIO42 | GPIO42 | **GPIO16** |
| IO expander | none | **TCA9554** | **TCA9554** |
| IDF floor | ≥ 5.5 | ≥ 5.5 | ≥ 5.3 |
| 2-px dirty-area rounder | yes | yes | yes |
| Brightness | panel cmd `0x51` | panel cmd `0x51` | panel cmd `0x51` |

Two of those rows are silent-corruption traps if missed: **byte order**
(`BIGENDIAN 1 → 0` swaps every pixel's colour on the 1.75/1.8) and the
**gap/MADCTL pair** (`main/rotation.c` + `torget_display_rotation_set()` carry
a hand-calibrated 4-mode `GAP[4][2]` table that is only correct for the 2.16's
`0xA0` boot orientation).

### The consent-button problem

VibePulse's OTA and Wi-Fi-setup consent model rests on a **physical 3 s hold on
KEY3 = GPIO18, active low** (`main/main.c:587`, `spec/hardware-capabilities.yaml`
`input.key3`). It is deliberately the one thing a script cannot do.

On the **1.75 there is no KEY3**: GPIO18 is expansion-header pin 7, a plain
GPIO `[hw-ref]`. The replacements that board actually offers are:

- **BOOT on GPIO0** — externally pulled up, reads low while pressed `[hw-ref]`.
  It is a strapping pin; holding it across a reset enters download mode.
- **PWR button via the TCA9554** — the conditioned `SYS_OUT` state is readable
  on expander pin **P4/EXIO4**, high while pressed, and reading it needs no
  AXP2101 register transaction `[hw-ref]`. (The 2.16 exposes the same signal
  directly on GPIO16.)

Neither is verified. For the 1.8, 2.41 and 1.9, the available consent control
is `[unknown]`. **A board with no reviewed physical consent control must refuse
to open the OTA window at all** — degrading to "the touchscreen can open it" is
a change to the security model, not a port detail, and needs an explicit
decision (see §5, Decision 3).

---

## 2. Audit: what in this repo is actually board-specific

Good news first: the split is far better than it looks. `tools/tokenserver/`,
every parser, every policy core, the relays and the whole host test suite are
board-independent and stay that way. The port surface is the host layer, the
layout constants, and the validation apparatus.

### 2.1 Host layer — the real code work

| Where | What is pinned |
|---|---|
| `main/idf_component.yml` | one hard-coded BSP dependency, `waveshare/esp32_s3_touch_amoled_2_16: "^2.0.1"` |
| `main/main.c:684` `display_start()` | our own display start (deliberately, to get `DISPLAY_FLUSH_ROWS 12` instead of the BSP's 50). Speaks `esp_lv_adapter_*` directly — **does not exist on the 1.8**, which ships `esp_lvgl_port` |
| `main/main.c:104` UI lock | `esp_lv_adapter_lock(-1)`; same problem |
| `main/main.c:743` `torget_display_rotation_set()` | `MADCTL[4]` + `GAP[4][2]`, calibrated for the 2.16 only |
| `main/main.c:718` touch flags | `swap_xy=1, mirror_y=1` — the 1.75 needs `mirror_x=1, mirror_y=1` |
| `main/main.c:587` KEY3 | `gpio_get_level(GPIO_NUM_18) == 0` |
| `main/main.c:436,554` | `DISPLAY_FLUSH_ROWS * 480 * 2` DMA floor and the low-DMA alarm |
| `main/rotation.c` | `SG_QUAD_UP`, `SG_QUAD_DIR`, `TOUCH_CW`, and `479 - x` touch rotation — all per-board empirical constants |
| `sdkconfig.defaults` | 16 MB QIO flash, octal PSRAM 80 MHz; correct for all five *so far* but must be confirmed per board |
| `partitions.csv` | 2 × 5 MB OTA slots — fits any 16 MB board; a 32 MB or 8 MB board changes it |

### 2.2 Layout — the visual work

- `components/app_tokens/vibepulse_layout.generated.h` — generated, single
  profile, `VP_SCREEN_W/H 480`, `VP_PERCENT_FONT_PX 164`.
- `components/app_tokens/usage_screen.c` — ~60 absolute Y/X constants
  (`HEADER_LINE_Y 63`, `PAGER_Y 456`, `RIGHT_STAT_X 240`, the whole `MT_*`
  Max-Tracker grid) plus literal `448`-wide boxes at lines 401 and 483.
- `components/app_tokens/agent_monitor.c` — nine `480`-wide text rows with
  absolute Y (278, 332, 424, 440, 274, 324, 420, 300, 358).
- `platform/torget_ui.c:154,246,258`, `platform/boot_screen.c:25`,
  `components/torget_ota/ota_ui.c:101`,
  `components/torget_wifi/wifi_setup_ui.c:106` — `lv_obj_set_size(x, 480, 480)`.
- `platform/fonts/` — 24 pre-rasterised sizes. The hero percentage is
  `plex_num_164` and `usage_screen.c:53` `_Static_assert`s it. **164 px does
  not fit 368 px or 240 px wide panels**; each new profile needs its own
  generated sizes.

Total literal `480`s outside the fonts: **~40 occurrences across 12 files**,
several of them in comments. That is the whole mechanical part of the layout
port, and it is small.

### 2.3 Validation apparatus — the part that decides how fast this goes

- `tools/vibepulse_studio/design.py` — reads `display.amoled` `width`/`height`
  from the hardware registry and validates one design document against it. Its
  validator (on-screen bounds, reading-order gaps, minimum row steps) is
  already resolution-general; it just needs to be run per board.
- `tools/preview-ui.sh` — builds the sim, runs `--vibepulse-static-qa`, and
  asserts **~150 named captures** all exactly `display.amoled` sized.
- `sim/main.c:1411` — `lv_sdl_window_create(480, 480)`.
- `test/test_vibepulse_visual_landmarks.py` — **1445 lines** of exact-pixel
  assertions, half derived from the generated header, half re-stating the
  `MT_*` constants verbatim.
- `tools/hardware_registry.py:97,288` — enforces **one** `board:` for the whole
  registry, and that every verification unit's board matches it.

That capture set is the single biggest asset here. Once the simulator can be
told which board to render, **every screen of every board can be reviewed at
exact size, in this repo, before a single panel is unboxed.**

### 2.4 One safety hole worth closing this week

`components/torget_ota/ota_service.c:174` accepts any incoming image whose
`esp_app_desc_t.project_name == "torget"` and whose `chip_id` is ESP32-S3.
Every one of these five boards builds a project named `torget` on an ESP32-S3.
So with five panels on the shelf, **`tools/ota-flash.sh <ip>` will happily push
the 480 × 480 build onto the 466 × 466 round panel**, pass magic/chip/project/
SHA-256, pass the boot-health gate (display init succeeds, UI builds, scheduler
ticks), and never roll back. The glass just ends up wrong forever. This becomes
likely the moment there is more than one board in the house.

---

## 3. Work packages — everything here needs no hardware

Ordered so each unblocks the next. WP1–WP4 are the ones that convert waiting
time into finished port.

### WP1 · Multi-board hardware registry *(foundation, do first)*

`spec/hardware-capabilities.yaml` is single-board by construction. Restructure
to `spec/boards/<board-id>/hardware-capabilities.yaml` with the source registry
staying shared, teach `tools/hardware_registry.py` a board argument, and keep
`device-units.yaml` global with each unit naming its board.

Then **seed one registry per new board now**, from the facts in §1: resolution,
driver, byte order, bus, pins, touch controller, IO expander, LVGL glue,
IDF floor, gap/MADCTL behaviour. Every state gets `bsp_support: yes`,
`unit_verified: unknown`, `confidence: source_inspected`. When the boxes
arrive, bring-up becomes *flipping evidence rows*, not research.

New source entries needed in `hardware-sources.yaml`: the BSP 3.0.1 / 2.0.3
components and the per-board hardware references, each with a rank.

### WP2 · A board abstraction in the firmware

Introduce `components/torget_board/` exporting one small interface, with one
implementation per board:

```c
typedef struct {
  const char *id;                 /* "amoled-2.16", "amoled-1.75", ... */
  int  width, height;
  bool round;                     /* 1.75 */
  esp_err_t (*display_start)(lv_indev_t **out_touch);
  void      (*ui_lock)(void);     /* adapter vs lvgl_port */
  void      (*ui_unlock)(void);
  bool      (*ui_try_lock)(uint32_t ms);
  esp_err_t (*rotation_set)(int quarter_turns);
  void      (*brightness_set)(int percent);
  bool      (*consent_held)(void);  /* KEY3 / EXIO4 / BOOT / none */
  size_t    flush_dma_bytes;
} tg_board;
```

This is where the 1.8's `esp_lvgl_port` divergence gets absorbed instead of
forking `main.c`. Board selection via a `TORGET_BOARD` CMake cache variable
plus per-board `sdkconfig.defaults.<board>`; the BSP dependency is selected
with `rules:`/`if:` in `main/idf_component.yml` or by a per-board component
manifest — verify which the pinned component-manager version supports before
committing to one.

`flush_dma_bytes` per board at `DISPLAY_FLUSH_ROWS = 12`: 2.16 → 11 520,
1.75 → 11 184, 1.8 → 8 832, 2.41 → 14 400, 1.91 → 5 760. The 2.41 is the
hungriest and needs its own measured budget before anyone trusts 12 rows there.

### WP3 · Layout profiles

1. Sweep the 32 literal `480`s and the absolute coordinates in
   `usage_screen.c` / `agent_monitor.c` into tokens (`VP_SCREEN_W`, or values
   derived from it). Purely mechanical; the visual landmark tests prove nothing
   moved on the 2.16.
2. Extend `design.py` to a `design/vibepulse/<board>/studio-design.json` per
   board, each validated against that board's registry dimensions, emitting
   `vibepulse_layout.<board>.generated.h`. The existing validator carries over
   for free — it already refuses off-screen and overlapping geometry.
3. Derive, then correct, the four new profiles. §3a below shows how much
   a uniform scale actually buys per board; what it never buys is these:
   - **466 round** — nearest sibling to today's, but the corners are gone.
     See §3a: a uniform 0.971 shrink puts 8 of 13 element groups off the
     glass, all of them full-width rows near the top or bottom edge. The fix
     is narrowing rows toward the poles, not shrinking the page.
   - **368 × 448 portrait** — narrower and shorter. The 164 px hero must drop
     (≈120–128 px), the 4-column stat row wants 2 × 2, the Max Tracker needs
     fewer weeks or a smaller cell.
   - **600 × 450 landscape** — the first landscape layout. Wider than tall
     inverts the whole vertical stack; the Needs You takeover and the launcher
     both assume portrait-ish balance.
   - **240 × 536 strip** (if the 1.9 is the 1.91) — not a rescale. Probably one
     metric per screen and a different page model.

### 3a · "Can we just scale the UI?"

Worth answering with numbers, because it is the difference between authoring
four layouts and generating them.

**Not at runtime.** LVGL can affine-transform an object tree, but every font
here is a pre-rasterised 4-bpp bitmap; scaling them resamples glyph bitmaps
and the result is soft on a panel whose whole premise is a crisp number read
from across a room. `.claude/skills/iterating-esp32-amoled-ui/SKILL.md` already
forbids it in as many words ("Do not scale or recolor at runtime"), and a
per-frame transform is exactly the wrong thing to add to a pipeline that is
already watched for DMA starvation.

**Yes at build time.** Regenerating the layout tokens *and* the fonts at a
scale factor keeps every glyph native and costs nothing at runtime. Font size
is not the obstacle people assume: all 24 rasters together are **~200 KB** of
glyph data, and with one binary per board only that board's set links in.

How far a uniform fit-inside scale actually gets each board:

| Board | Panel | Aspect | Fit scale | Hero px | Panel area used | What scaling alone leaves |
|---|---|---|---|---|---|---|
| 2.16 | 480 × 480 | 1.00 | 1.000 | 164 | 100 % | baseline |
| 1.75 | 466 × 466 | 1.00 | 0.971 | 159 | 100 % | geometry fits; **the round corners do not** |
| 1.8 | 368 × 448 | 0.82 | 0.767 | 126 | 82 % | 80 px of dead height |
| 2.41 | 600 × 450 | 1.33 | 0.938 | 154 | 75 % | **150 px of unused width** on the biggest panel |
| 1.91? | 240 × 536 | 0.45 | 0.500 | 82 | 45 % | 296 px of dead height — not a layout, a different product |

And on the 1.75 specifically, scaling every current element by 0.971 and
testing each corner against the 233 px radius:

| Element | Verdict at 466 round |
|---|---|
| percent hero, progress bar, pager dots, Max Tracker legend | fit (4–26 px margin) |
| quota row, reset row, Max Tracker grid, Max Tracker stat row | **off glass by 4–34 px** |
| content safe area, provider row, status block, header hairline, Wi-Fi badge | **off glass by 39–69 px** |

Every failure is a full-width row sitting near the top or bottom pole, where a
circle is narrowest — including the Wi-Fi badge at (426, 28), which lands 53 px
outside the glass entirely. That is a systematic, fixable shape: a round panel
needs a **circular** safe area, so row width must taper with distance from
centre, and corner-anchored badges must move inboard.

**So the plan is scale-then-reflow, not scale-or-author.** Give `design.py` a
mode that derives a candidate profile from the 480 reference by scale factor,
emit it, then hand-correct against the validator and the circle gate (WP7).
That turns WP3 from "author four layouts" into "review four generated layouts",
and it is roughly: 1.75 ≈ 90 % generated, 1.8 ≈ 60 %, 2.41 ≈ 40 %, 1.91 ≈ 0 %.

This also argues for one extra token in the profile — `VP_SAFE_SHAPE`
(`rect` | `circle`) — so the validator can check the right envelope instead of
assuming a rectangle.

### WP4 · Fonts for the new profiles

`platform/fonts/fetch-and-convert.sh` regenerates everything from IBM Plex with
`lv_font_conv`; it needs `npx` and network, not hardware. Once WP3 fixes each
profile's hero size, generate and commit those rasters. Doing this early
matters because the exact rendered line height feeds back into `design.py`'s
overlap validator (`PERCENT_RENDERED_LINE_HEIGHT_PX`), which is per-size.

Watch flash: 24 fonts already; four more profiles' worth of hero sizes is real
size. Prefer sharing sizes across profiles where the design allows, and
consider building only the selected board's fonts (`FONTS` is a glob in both
`sim/CMakeLists.txt` and the component).

### WP5 · Multi-resolution simulator + preview

`sim/main.c` takes `--board <id>`, sizes the SDL window from the board profile,
and `tools/preview-ui.sh <app> <board>` asserts the capture set at that board's
dimensions. **This is the acceleration.** After WP3–WP5, `preview-ui.sh` gives
~150 exact-size frames per board on a laptop, and the panel review on arrival
becomes a confirmation rather than a discovery.

### WP6 · Per-board visual landmark tests

Parameterise `test_vibepulse_visual_landmarks.py` over the board profile,
sourcing the `MT_*` constants from the generated header instead of restating
them. Run the 2.16 lane in CI as today plus one lane per new board, so a
layout profile cannot rot.

### WP7 · Round-panel geometry gate *(new, cheap, high value)*

A validator that fails when any non-black pixel in a 466 capture falls outside
the inscribed circle minus a bezel margin. Round panels break square layouts
silently — a clipped corner is invisible in a square preview and obvious on
glass. Writable and unit-testable today against synthetic frames.

### WP8 · Cross-board OTA guard *(safety, do this week)*

Close §2.4 before there are five panels on one LAN:

- carry the board id in the image (project name `torget-<board>`, or a board
  field checked in `image_prefix_valid()`);
- reject a mismatching image at the device, with an honest message on glass;
- teach `tools/ota-flash.sh` and the tokenserver's `otaAvailableVersion` to be
  per-board, so a panel is never told an update is ready that it must refuse;
- host tests in `test/test_ota_*.py` covering accept-own / reject-foreign.

Pure firmware + host work, fully testable now, and it is what stops an
afternoon of confusion later.

### WP9 · Board-identity probe

A tiny bring-up mode (build flag or a `torget_probe` app) that at boot scans
I2C and logs every address that answers, reads the panel/touch chip IDs, and
prints the compiled-in board profile against what it found. On arrival this
answers "is my 1.8 a V1 (SH8601 + FT3168) or a V2 (CO5300 + CST816S/FT5x06)?"
and "is my 2.41 a V1 or V2 pinout?" in one flash instead of an evening of
photographs. Write it now; it costs an hour and saves the first day.

### WP10 · Memory budget model

Parameterise the DMA floor and low-block alarm on `BSP_LCD_H_RES`
(`main/main.c:436,554`, `components/torget_wifi/wifi_setup.h:31`,
`main/lv_psram_pool.h`). Extend `test/test_target_tls_memory.py` and the LVGL
memory guard to reason per board. The 1.8's **single**-buffer `esp_lvgl_port`
path has a different profile entirely and must not inherit the 2.16's numbers.

### WP11 · Bring-up runbook + acceptance gate

Write `docs/board-bringup.md` before the boxes arrive (§4 below is its
skeleton), so day 1 is mechanical.

---

## 4. Day-1 runbook, per board

Common to all: identify the revision (silkscreen photo + WP9 probe) → confirm
flash/PSRAM against `sdkconfig.defaults` → USB flash over download mode
(hold BOOT, tap RESET) → power from a supply, not a laptop port → display,
touch, IMU, button, Wi-Fi, tokenserver, OTA, 24 h soak. Never flash without
explicit authorisation (`CLAUDE.md`).

### 1.75 — 466 × 466 round · *closest sibling, start here*

Everything about it is one step from the 2.16, which makes it the right first
port and the one that shakes out WP1–WP5.

1. BSP `waveshare/esp32_s3_touch_amoled_1_75: ^3.0.1`. Note `bsp_display_lock`
   is now `esp_err_t` — our `esp_lv_adapter_lock` route is unaffected, but the
   `spec/hardware.md` warning must be re-scoped to the 2.16 so nobody
   "re-fixes" a bug that no longer exists here.
2. Flip byte order to little-endian. Symptom if missed: correct layout,
   inverted-looking colours.
3. Rebuild the rotation table from scratch: no `MADCTL` in init means boot
   orientation is `0x00`, not `0xA0`, and the BSP already applies
   `set_gap(6, 0)`. Recalibrate `GAP[4][2]` with the structured four-mode test
   from `main.c`'s comment — never photo forensics.
4. Touch flags `swap_xy=0, mirror_x=1, mirror_y=1`, and recalibrate
   `SG_QUAD_UP` / `SG_QUAD_DIR` / `TOUCH_CW` in `rotation.c`.
   `read_rotated()`'s `479 - x` becomes `465 - x` — parameterise it.
5. **Consent path**: decide between BOOT (GPIO0) and PWR-via-TCA9554 EXIO4, and
   verify the chosen one physically before any OTA window can open.
6. Run WP7's circle gate against the real captures, then review the physical
   panel statically before any motion work.

### 1.8 — 368 × 448 portrait · *the LVGL-glue port*

1. **First, establish the revision.** BSP 2.0.3 drives CO5300 and probes
   CST816S then FT5x06. A V1 board (SH8601 + FT3168 `[web]`) is not covered by
   that BSP and is a materially bigger job. WP9's probe answers this in one
   flash.
2. Absorb `esp_lvgl_port` behind WP2's interface — `lvgl_port_lock/unlock`
   instead of `esp_lv_adapter_*`, and a different display-start call shape.
   Re-derive the lock-truthfulness question for this path from *its* source; do
   not assume the 2.16's inverted-bool story applies.
3. Single-buffer draw path: re-measure the memory budget from zero. Do not
   carry `DISPLAY_FLUSH_ROWS 12` over as if it were verified.
4. New pin map: PCLK 11, touch INT 21, MCLK 16, LCD/touch reset via TCA9554.
5. Portrait layout profile + smaller hero fonts (WP3/WP4).
6. Consent control `[unknown]` — establish it before OTA.

### 2.41 — 600 × 450 landscape · *board support written from scratch*

1. **No Waveshare BSP exists** for this board in the components repo. Plan for
   `esp_lcd_rm690b0` (or Waveshare's driver if one appears) + an FT6336 touch
   driver, wired by hand: QSPI bus, panel init sequence, gap, brightness,
   backlight-free AMOLED brightness via `0x51`, and the 2-px rounder.
2. **Two hardware revisions (V1/V2) with different display, touch, interrupt
   and expander pins** `[web]`. Get the schematic for the exact revision
   shipped; WP9's probe plus the silkscreen decides which.
3. First landscape layout in the project — the largest design job of the four.
4. Largest flush DMA need (14 400 B at 12 rows). Budget explicitly.
5. Realistically the last of the four, and the one most likely to need its own
   focused issue per issue #5's guidance.

### "1.9" — unidentified

Do nothing beyond WP1 seeding until the SKU is confirmed. If it is the 1.91
(240 × 536, RM67162, FT3168), treat it as a **new product surface**, not a port:
a 2.23:1 strip cannot carry the current pages, and the honest answer may be a
reduced VibePulse (one metric + Needs You) rather than all eight pages.

---

## 5. Decisions needed from you

1. **How many boards are actually targets?** Doing 1.75 + 1.8 properly is a
   different project from doing all five. Recommendation: 1.75 first as the
   pathfinder, 1.8 second, 2.41 as its own issue, 1.9 deferred until identified.
2. **One firmware or one per board?** Recommendation: one binary per board,
   selected at build time by `TORGET_BOARD` — runtime detection would carry
   every board's fonts and layouts in flash. This makes WP8 mandatory.
3. **Consent model on boards without KEY3.** Options: (a) an equivalent
   physical control per board (BOOT, PWR/EXIO4), reviewed like KEY3 was;
   (b) the OTA window refuses to open, USB-only on those boards; (c) a
   touch-based opener. (c) is a weakening of a deliberate security property and
   should not be adopted by default.
4. **Does "VibePulse works on board X" mean pixel-perfect or functional?** A
   defensible acceptance gate per board: display + touch + Wi-Fi + tokenserver
   + one full page set at exact size + OTA round-trip + boot-health + 24 h soak,
   with a physical review recorded under `docs/superpowers/reviews/`, before
   README claims support.

## 6. Suggested order for the week before hardware

Day 1–2: WP1 (registry) + WP8 (OTA guard) + WP9 (probe).
Day 3–4: WP2 (board interface) + WP10 (memory model) — the 2.16 must stay
byte-identical through both; the existing landmark tests are the proof.
Day 5–7: WP3 + WP4 + WP5 for the **1.75 only**, ending with a full ~150-frame
466 × 466 capture set to review, plus WP7's circle gate.

If that lands, the 1.75 arrives to a firmware that already builds for it, a
registry that already describes it, an OTA path that cannot be cross-flashed,
and a reviewed set of exact-size frames to compare the glass against. The
remaining unknowns are then genuinely physical: colours, rotation constants,
touch mapping, the consent button, and memory under real TLS load.

## 7. Sources

- Waveshare BSP source, cloned 2026-08-28:
  [`waveshareteam/Waveshare-ESP32-components`](https://github.com/waveshareteam/Waveshare-ESP32-components)
  — `bsp/esp32_s3_touch_amoled_{2_16,1_75,1_8}`.
- [`waveshareteam/ESP32-S3-Touch-AMOLED-1.75`](https://github.com/waveshareteam/ESP32-S3-Touch-AMOLED-1.75)
  `HARDWARE_REFERENCE.md` — GPIO map, I2C addresses, BOOT/PWR button facts.
- [ESP32-S3-Touch-AMOLED-1.75 product page](https://www.waveshare.com/esp32-s3-touch-amoled-1.75.htm)
- [ESP32-S3-Touch-AMOLED-1.8 product page](https://www.waveshare.com/esp32-s3-touch-amoled-1.8.htm)
- [ESP32-S3-Touch-AMOLED-2.41 product page](https://www.waveshare.com/esp32-s3-touch-amoled-2.41.htm)
  and [wiki](https://www.waveshare.com/wiki/ESP32-S3-Touch-AMOLED-2.41)
- [ESP32-S3-AMOLED-1.91 product page](https://www.waveshare.com/esp32-s3-amoled-1.91.htm)
  (candidate for the "1.9")
- This repository: `spec/hardware.md`, `spec/hardware-capabilities.yaml`,
  `main/main.c`, `main/rotation.c`, `components/app_tokens/usage_screen.c`,
  `tools/vibepulse_studio/design.py`, `tools/preview-ui.sh`,
  `test/test_vibepulse_visual_landmarks.py`,
  `components/torget_ota/ota_service.c`.
