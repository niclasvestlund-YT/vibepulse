# VibePulse Studio Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a fast local VibePulse design tool that always shows the UI on a true 480 x 480 canvas, saves reviewed design tokens, exports exact-size references, and verifies the same values through the existing LVGL simulator before any physical flash.

**Architecture:** Run a dependency-light Python service bound to localhost and a plain HTML/SVG interface with no Node build. The browser supplies immediate design feedback; generated C constants feed the real shared LVGL code, and the existing SDL simulator remains the pixel authority. The hardware registry supplies immutable device facts, while the Studio design JSON owns only approved VibePulse visual tokens.

**Tech Stack:** Python 3.11+, standard-library HTTP server, PyYAML, Pillow, HTML/CSS/ES modules, SVG, C11, LVGL 9.5, SDL2/Ninja simulator.

---

## Prerequisite

Complete `2026-08-10-hardware-capability-registry-implementation.md` first. This plan reads `spec/hardware-capabilities.yaml` and uses the shared `requirements-dev.txt` created there.

## File responsibility map

- `tools/preview-ui.sh`: one command that incrementally builds the simulator, captures VibePulse, and converts only fresh 480 x 480 images.
- `tools/vibepulse_studio/design.py`: validate, atomically save, and generate C constants from the design document.
- `tools/vibepulse_studio/server.py`: localhost-only API and static-file server.
- `tools/vibepulse_studio/test_design.py`: lock schema, generated values, and immutable device facts.
- `tools/vibepulse_studio/test_server.py`: verify safe paths, atomic writes, and exact PNG dimensions.
- `tools/vibepulse_studio/web/index.html`: Studio shell and true-size preview region.
- `tools/vibepulse_studio/web/studio.css`: visual hierarchy and explicit 1:1/2:1 preview scaling.
- `tools/vibepulse_studio/web/studio.js`: render SVG states, edit bounded tokens, save, and export.
- `tools/vibepulse_studio/web/fonts/`: local IBM Plex Sans WOFF2 files and OFL license.
- `design/vibepulse/studio-design.json`: reviewed visual source for the supported VibePulse states.
- `design/vibepulse/exports/`: committed 480 x 480 PNG references for approved states.
- `components/app_tokens/vibepulse_layout.generated.h`: deterministic layout/palette constants consumed by LVGL.
- `components/app_tokens/usage_screen.c`: replace matching literal constants with generated names; behavior/data stay unchanged.
- `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`: shared fast-loop instructions discovered by Claude Code.
- `tools/install-local-skills.sh`: link Codex's personal skill directory to the canonical project skill.
- `AGENTS.md` and `CLAUDE.md`: route both agents through Studio and the physical AMOLED gate.
- `test/test_vibepulse_studio_wiring.py`: repository-level workflow and no-duplication checks.
- `test/run.sh`: include Studio unit and wiring tests.
- `docs/superpowers/reviews/2026-08-10-vibepulse-studio-static-amoled.md`: physical review record immediately after static integration.

### Task 1: Make the existing simulator a reliable one-command image gate

**Files:**
- Create: `tools/preview-ui.sh`
- Create: `test/test_preview_ui.py`
- Modify: `test/run.sh`

- [ ] **Step 1: Write the failing shell-wiring test**

Create `test/test_preview_ui.py`:

```python
from pathlib import Path

root = Path(__file__).resolve().parents[1]
script = (root / "tools/preview-ui.sh").read_text(encoding="utf-8")

assert "mktemp -d" in script
assert "--vibepulse-static-qa" in script
assert "cmake --build" in script
assert "Image.open" in script
assert "load_registry" in script
assert "image.size != expected" in script
assert "/tmp/torget-vibepulse-*.bmp" in script
print("OK: exact-size VibePulse preview workflow")
```

- [ ] **Step 2: Run the test and verify the script is missing**

Run:

```bash
python3 test/test_preview_ui.py
```

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Implement the exact-size preview script**

Create executable `tools/preview-ui.sh`:

```sh
#!/bin/sh
set -eu

if [ "${1:-}" != "vibepulse" ]; then
  echo "usage: $0 vibepulse" >&2
  exit 2
fi

repo=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
out=$(mktemp -d "${TMPDIR:-/tmp}/vibepulse-preview.XXXXXX")

cmake -S "$repo/sim" -B "$repo/sim/build" -G Ninja
cmake --build "$repo/sim/build"
rm -f /tmp/torget-vibepulse-*.bmp
"$repo/sim/build/torget-sim" --vibepulse-static-qa

python3 - "$repo" "$out" <<'PY'
import glob
import sys
from pathlib import Path
from PIL import Image

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
from tools.hardware_registry import load_registry

out = Path(sys.argv[2])
registry = load_registry(repo / "spec")
display = registry.capabilities["display.amoled"]["properties"]
expected = (display["width"], display["height"])
paths = sorted(glob.glob("/tmp/torget-vibepulse-*.bmp"))
if not paths:
    raise SystemExit("no fresh VibePulse captures")
for source in paths:
    image = Image.open(source)
    if image.size != expected:
        raise SystemExit(f"wrong capture size: {source}: {image.size}")
    target = out / (Path(source).stem.removeprefix("torget-") + ".png")
    image.convert("RGB").save(target)
    print(target)
PY
```

- [ ] **Step 4: Wire the test into the host suite and run it**

Add `python3 test_preview_ui.py` beside the existing Python wiring tests in `test/run.sh`.

Run:

```bash
python3 test/test_preview_ui.py
./tools/preview-ui.sh vibepulse
```

Expected: the wiring test passes and the script prints a fresh temporary directory containing eight 480 x 480 PNGs.

- [ ] **Step 5: Commit the capture gate**

```bash
git add tools/preview-ui.sh test/test_preview_ui.py test/run.sh
git commit -m "Add exact-size VibePulse preview command"
```

### Task 2: Define one versioned design contract and deterministic C export

**Files:**
- Create: `design/vibepulse/studio-design.json`
- Create: `tools/vibepulse_studio/__init__.py`
- Create: `tools/vibepulse_studio/design.py`
- Create: `tools/vibepulse_studio/test_design.py`
- Create: `components/app_tokens/vibepulse_layout.generated.h`

- [ ] **Step 1: Write failing contract and generator tests**

Create `tools/vibepulse_studio/test_design.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from tools.vibepulse_studio.design import (
    DesignError, generate_header, load_design, load_display, save_design,
)


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[2]
        self.path = self.repo / "design/vibepulse/studio-design.json"
        self.display = load_display(self.repo / "spec")

    def test_repository_design_is_exact_and_provider_colors_are_locked(self):
        design = load_design(self.path, self.display)
        self.assertEqual(self.display["width"], 480)
        self.assertEqual(self.display["height"], 480)
        self.assertNotIn("canvas", design)
        self.assertEqual(design["palette"]["claude"], "#D97757")
        self.assertEqual(design["palette"]["codex"], "#6F78FF")
        self.assertEqual(design["palette"]["background"], "#000000")

    def test_device_facts_and_provider_colors_cannot_be_changed(self):
        design = json.loads(self.path.read_text())
        design["canvas"] = {"width": 481, "height": 480}
        with self.assertRaisesRegex(DesignError, "device facts"):
            generate_header(design, self.display)
        design = json.loads(self.path.read_text())
        design["palette"]["claude"] = "#FFFFFF"
        with self.assertRaisesRegex(DesignError, "Claude color"):
            generate_header(design, self.display)

    def test_header_generation_is_deterministic(self):
        design = load_design(self.path, self.display)
        first = generate_header(design, self.display)
        second = generate_header(design, self.display)
        self.assertEqual(first, second)
        self.assertIn("#define VP_SCREEN_W 480", first)
        self.assertIn("#define VP_COLOR_CLAUDE 0xD97757", first)

    def test_save_is_atomic_and_reloads(self):
        design = load_design(self.path, self.display)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "design.json"
            save_design(target, design, self.display)
            self.assertEqual(load_design(target, self.display), design)
            self.assertFalse((Path(tmp) / "design.json.tmp").exists())
```

- [ ] **Step 2: Create the approved seed design**

Create `design/vibepulse/studio-design.json` with this bounded contract:

```json
{
  "schemaVersion": 1,
  "deviceCapability": "display.amoled",
  "palette": {
    "background": "#000000",
    "text": "#FFFFFF",
    "muted": "#858C97",
    "track": "#303238",
    "hairline": "#202328",
    "claude": "#D97757",
    "codex": "#6F78FF"
  },
  "hero": {
    "safeX": 22,
    "contentWidth": 436,
    "providerY": 23,
    "quotaY": 86,
    "percentY": 112,
    "percentFontPx": 146,
    "barY": 276,
    "barHeight": 18,
    "resetY": 312,
    "statusY": 388,
    "statusHeight": 66
  },
  "fixtures": {
    "claude": {"provider": "CLAUDE", "model": "OPUS 5", "effort": "ULTRA", "quota": "FABLE · WEEK", "percent": 73, "today": 12, "reset": "RESET IN 2D 4H"},
    "codex": {"provider": "CODEX", "model": "GPT-5.6 SOL", "effort": "XHIGH", "quota": "WEEKLY", "percent": 43, "today": 8, "reset": "RESET FRI 10:29"}
  }
}
```

This seed does not add a 5-hour row, persistent bottom working bar, or VibePulse logo. Those choices reflect the latest approved direction and can be changed only through a new reviewed design revision.

- [ ] **Step 3: Run tests and verify the design module is missing**

Run:

```bash
python3 -m unittest tools.vibepulse_studio.test_design -v
```

Expected: FAIL because `design.py` does not exist.

- [ ] **Step 4: Implement validation, atomic saving, and header generation**

Implement `tools/vibepulse_studio/design.py` with these fixed mappings:

```python
import json
import os
from pathlib import Path

from tools.hardware_registry import load_registry


class DesignError(ValueError):
    pass


LOCKED_COLORS = {
    "background": "#000000",
    "claude": "#D97757",
    "codex": "#6F78FF",
}

TOKEN_NAMES = {
    "safeX": "VP_SAFE_X",
    "contentWidth": "VP_CONTENT_W",
    "providerY": "VP_PROVIDER_Y",
    "quotaY": "VP_QUOTA_Y",
    "percentY": "VP_PERCENT_Y",
    "percentFontPx": "VP_PERCENT_FONT_PX",
    "barY": "VP_BAR_Y",
    "barHeight": "VP_BAR_H",
    "resetY": "VP_RESET_Y",
    "statusY": "VP_STATUS_Y",
    "statusHeight": "VP_STATUS_H",
}


def load_display(spec_dir):
    registry = load_registry(spec_dir)
    properties = registry.capabilities["display.amoled"]["properties"]
    return {"width": properties["width"], "height": properties["height"]}


def validate_design(value, display):
    if value.get("schemaVersion") != 1:
        raise DesignError("schemaVersion must be 1")
    if "canvas" in value or value.get("deviceCapability") != "display.amoled":
        raise DesignError("device facts belong only in the hardware registry")
    palette = value.get("palette", {})
    for name, expected in LOCKED_COLORS.items():
        if palette.get(name) != expected:
            label = name.title() if name != "claude" else "Claude"
            raise DesignError(f"{label} color must remain {expected}")
    hero = value.get("hero", {})
    if set(hero) != set(TOKEN_NAMES):
        raise DesignError("hero keys do not match the v1 contract")
    if not all(isinstance(v, int) for v in hero.values()):
        raise DesignError("hero values must be integer panel pixels")
    if hero["safeX"] < 16 or hero["safeX"] > 40:
        raise DesignError("safeX outside reviewed range")
    if hero["contentWidth"] != display["width"] - 2 * hero["safeX"]:
        raise DesignError("contentWidth must match safeX")
    if hero["barHeight"] < 12 or hero["barHeight"] > 24:
        raise DesignError("barHeight outside readable range")
    return value


def load_design(path, display):
    return validate_design(json.loads(Path(path).read_text(encoding="utf-8")),
                           display)


def save_design(path, value, display):
    validate_design(value, display)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)


def generate_header(value, display):
    validate_design(value, display)
    lines = [
        "/* Generated by tools/vibepulse_studio/design.py; do not hand-edit. */",
        "#ifndef VIBEPULSE_LAYOUT_GENERATED_H",
        "#define VIBEPULSE_LAYOUT_GENERATED_H",
        f"#define VP_SCREEN_W {display['width']}",
        f"#define VP_SCREEN_H {display['height']}",
    ]
    for source, target in TOKEN_NAMES.items():
        lines.append(f"#define {target} {value['hero'][source]}")
    for name, color in value["palette"].items():
        lines.append(f"#define VP_COLOR_{name.upper()} 0x{color[1:].upper()}")
    lines.extend(["#endif", ""])
    return "\n".join(lines)
```

Add a CLI accepting `--check` or `--write`; `--check` exits nonzero when the committed header differs from generated output.

- [ ] **Step 5: Generate the header, run tests, and commit**

Run:

```bash
python3 tools/vibepulse_studio/design.py --write
python3 -m unittest tools.vibepulse_studio.test_design -v
python3 tools/vibepulse_studio/design.py --check
```

Expected: all pass and the second generator invocation produces no diff.

Commit:

```bash
git add design/vibepulse/studio-design.json tools/vibepulse_studio components/app_tokens/vibepulse_layout.generated.h
git commit -m "Define VibePulse Studio design contract"
```

### Task 3: Build a localhost-only Studio service

**Files:**
- Create: `tools/vibepulse_studio/server.py`
- Create: `tools/vibepulse_studio/test_server.py`

- [ ] **Step 1: Write failing API tests**

Test a `StudioApplication.handle(method, path, body)` unit without opening a real port:

```python
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.vibepulse_studio.design import (
    load_design, load_display, save_design,
)
from tools.vibepulse_studio.server import StudioApplication

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_DESIGN = REPOSITORY_ROOT / "design/vibepulse/studio-design.json"


class StudioApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = REPOSITORY_ROOT / "spec"
        self.display = load_display(self.spec)
        self.design_path = self.repo / "design.json"
        save_design(self.design_path,
                    load_design(REPOSITORY_DESIGN, self.display), self.display)
        self.app = StudioApplication(self.repo, self.design_path, self.spec)

    def tearDown(self):
        self.temp.cleanup()

    def test_get_design_returns_json(self):
        status, headers, body = self.app.handle("GET", "/api/design", b"")
        self.assertEqual(status, 200)
        self.assertNotIn("canvas", json.loads(body))
        status, headers, body = self.app.handle("GET", "/api/hardware", b"")
        self.assertEqual(json.loads(body)["display"]["width"], 480)

    def test_invalid_save_does_not_replace_design(self):
        bad = json.loads(self.design_path.read_text())
        bad["canvas"] = {"width": 1000, "height": 480}
        status, _, _ = self.app.handle("PUT", "/api/design", json.dumps(bad).encode())
        self.assertEqual(status, 422)
        self.assertNotIn("canvas", load_design(self.design_path, self.display))

    def test_export_rejects_non_480_png(self):
        image = Image.new("RGB", (960, 960), "black")
        payload = io.BytesIO()
        image.save(payload, "PNG")
        status, _, _ = self.app.handle("POST", "/api/export/claude", payload.getvalue())
        self.assertEqual(status, 422)

    def test_path_traversal_is_rejected(self):
        status, _, _ = self.app.handle("GET", "/../../secrets.h", b"")
        self.assertEqual(status, 404)
```

- [ ] **Step 2: Run tests and verify the server class is missing**

Run:

```bash
python3 -m unittest tools.vibepulse_studio.test_server -v
```

Expected: FAIL importing `StudioApplication`.

- [ ] **Step 3: Implement the application boundary**

Implement these routes in `StudioApplication`:

```text
GET  /api/design                  validated design JSON
PUT  /api/design                  validate, atomically save, regenerate header
GET  /api/hardware               display/touch/AMOLED subset from registry
POST /api/export/{state}         PNG matching registered display dimensions
GET  /                            web/index.html
GET  /studio.css                  fixed static asset
GET  /studio.js                   fixed static asset
GET  /fonts/{known-file}          allowlisted local font only
```

Use explicit route matching, a 1 MiB request cap, JSON error bodies, and a fixed allowlist for static assets. Never concatenate an untrusted URL path onto the repository path. Load display dimensions from `spec/hardware-capabilities.yaml` once at startup. On a successful design save, call `save_design()` with those dimensions first, write the generated header atomically second, and return `{design, headerDigest}`.

- [ ] **Step 4: Bind the development server safely**

The CLI must default to `127.0.0.1:64942`, reject a non-loopback bind unless `--allow-lan` is explicit, print the URL, and optionally open it with `webbrowser.open`. Use `ThreadingHTTPServer`; set request timeouts and suppress bodies/tokens from logs.

- [ ] **Step 5: Run API tests and commit**

Run:

```bash
python3 -m unittest tools.vibepulse_studio.test_design tools.vibepulse_studio.test_server -v
```

Expected: all pass.

Commit:

```bash
git add tools/vibepulse_studio/server.py tools/vibepulse_studio/test_server.py
git commit -m "Add local VibePulse Studio service"
```

### Task 4: Build the exact-size SVG design interface

**Files:**
- Create: `tools/vibepulse_studio/web/index.html`
- Create: `tools/vibepulse_studio/web/studio.css`
- Create: `tools/vibepulse_studio/web/studio.js`
- Create: `tools/vibepulse_studio/web/fonts/IBMPlexSans-Bold.woff2`
- Create: `tools/vibepulse_studio/web/fonts/IBMPlexSans-SemiBold.woff2`
- Create: `tools/vibepulse_studio/web/fonts/OFL.txt`
- Create: `test/test_vibepulse_studio_wiring.py`

- [ ] **Step 1: Add failing UI wiring assertions**

Create `test/test_vibepulse_studio_wiring.py`:

```python
from pathlib import Path

root = Path(__file__).resolve().parents[1]
html = (root / "tools/vibepulse_studio/web/index.html").read_text()
css = (root / "tools/vibepulse_studio/web/studio.css").read_text()
js = (root / "tools/vibepulse_studio/web/studio.js").read_text()

assert 'viewBox="0 0 480 480"' not in html
assert 'width="480" height="480"' not in html
assert "scale(1)" in css and "scale(2)" in css
assert 'setAttribute("viewBox"' in js
assert 'setAttribute("width"' in js and 'setAttribute("height"' in js
assert "Preview:" in js
assert "/api/design" in js and "/api/hardware" in js
assert "/api/export/" in js
assert "5-HOUR" not in html
assert "VIBEPULSE" not in html
assert "#D97757" not in js and "#6F78FF" not in js
print("OK: Studio uses exact canvas and server-owned design facts")
```

- [ ] **Step 2: Build the static shell and SVG**

Create a two-column desktop shell: controls on the left and an unscaled 480 x 480 preview on the right. The preview must contain this exact root:

```html
<section aria-labelledby="preview-title">
  <header>
    <h2 id="preview-title"></h2>
    <button data-scale="1">1:1 physical pixels</button>
    <button data-scale="2">2:1 inspect</button>
  </header>
  <div id="preview-frame" class="scale-1">
    <svg id="device-preview" role="img"
         aria-label="VibePulse AMOLED preview">
      <rect id="screen-background" fill="var(--background)"/>
      <g id="hero-content"></g>
    </svg>
  </div>
</section>
```

After `/api/hardware` loads, JavaScript runs:

```javascript
const svg = document.querySelector("#device-preview");
const background = document.querySelector("#screen-background");
const {width, height} = state.hardware.display;
svg.setAttribute("width", String(width));
svg.setAttribute("height", String(height));
svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
background.setAttribute("width", String(width));
background.setAttribute("height", String(height));
document.querySelector("#preview-title").textContent =
    `Preview: ${width} × ${height}`;
```

The 1:1 control must not use responsive CSS scaling. The 2:1 mode may use `transform: scale(2)` but must display `INSPECTION ZOOM — NOT PHYSICAL SIZE` above it.

- [ ] **Step 3: Render only bounded, useful controls**

In `studio.js`, load `/api/design` and `/api/hardware`, then render provider, model/effort, quota label, large percent, `+N% TODAY`, reset, provider-color progress bar, and optional compact activity dot. Populate copy from the fixture; populate geometry and palette only from the design response.

Expose numeric inputs for `safeX`, `providerY`, `quotaY`, `percentY`, `barY`, `barHeight`, `resetY`, `statusY`, and `statusHeight`. Clamp each to the server-validated range. Do not add free dragging, arbitrary layers, gradients, shadows, a persistent bottom working rail, a VibePulse logo, or a 5-hour row.

Use a single `render(design, provider)` function. Provider switching changes fixture text and the progress fill color without changing layout. Add explicit buttons for `Claude`, `Codex`, `Missing`, and `Stale` states.

- [ ] **Step 4: Save and export reviewed states**

Implement:

```javascript
async function saveDesign() {
  const response = await fetch("/api/design", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(state.design),
  });
  if (!response.ok) throw new Error((await response.json()).error);
  const payload = await response.json();
  state.design = payload.design;
  state.headerDigest = payload.headerDigest;
  render();
}

async function exportPng(name) {
  await document.fonts.ready;
  const svg = new XMLSerializer().serializeToString(
      document.querySelector("#device-preview"));
  const blob = new Blob([svg], {type: "image/svg+xml"});
  const image = await createImageBitmap(blob);
  const {width, height} = state.hardware.display;
  const canvas = Object.assign(document.createElement("canvas"), {width, height});
  canvas.getContext("2d").drawImage(image, 0, 0, width, height);
  const png = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
  const response = await fetch(`/api/export/${name}`, {method: "POST", body: png});
  if (!response.ok) throw new Error((await response.json()).error);
}
```

Show save/export success inline. Do not use browser alerts and do not report success before the server response.

- [ ] **Step 5: Add the actual IBM Plex web fonts and license**

Download Bold and SemiBold WOFF2 from IBM Plex revision
`bf260093582f04622aacc1e9f9ca604d7ccd0c42` using the two URLs below, copy
that revision's `OFL.txt`, and load them with local `@font-face` rules:

```text
https://raw.githubusercontent.com/IBM/plex/bf260093582f04622aacc1e9f9ca604d7ccd0c42/packages/plex-sans/fonts/complete/woff2/IBMPlexSans-Bold.woff2
https://raw.githubusercontent.com/IBM/plex/bf260093582f04622aacc1e9f9ca604d7ccd0c42/packages/plex-sans/fonts/complete/woff2/IBMPlexSans-SemiBold.woff2
https://raw.githubusercontent.com/IBM/plex/bf260093582f04622aacc1e9f9ca604d7ccd0c42/LICENSE.txt
```

The Studio must work offline after checkout. The weights must match the existing LVGL font generation: 700 for hero numbers and 600 for UI text.

- [ ] **Step 6: Run focused tests and visually inspect at 1:1**

Run:

```bash
python3 test/test_vibepulse_studio_wiring.py
python3 -m unittest tools.vibepulse_studio.test_design tools.vibepulse_studio.test_server -v
python3 tools/vibepulse_studio/server.py --no-open
```

Open `http://127.0.0.1:64942`, verify the browser reports a 480 x 480 SVG, compare 1:1 against a 480 x 480 exported PNG, and stop the server. Expected: no network requests after initial local load and no console errors.

- [ ] **Step 7: Commit the Studio interface**

```bash
git add tools/vibepulse_studio/web test/test_vibepulse_studio_wiring.py
git commit -m "Build exact-size VibePulse Studio preview"
```

### Task 5: Connect the generated design to the shared LVGL implementation

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `test/test_vibepulse_layout_wiring.py`
- Modify: `test/run.sh`
- Create: `design/vibepulse/exports/claude.png`
- Create: `design/vibepulse/exports/codex.png`

- [ ] **Step 1: Add failing generated-token wiring checks**

Extend `test/test_vibepulse_layout_wiring.py`:

```python
assert '#include "vibepulse_layout.generated.h"' in source
for old in ("#define SAFE_X", "#define CONTENT_W", "#define HERO_BAR_Y",
            "#define HERO_BAR_H", "#define HERO_RESET_Y"):
    assert old not in source
for token in ("VP_SAFE_X", "VP_CONTENT_W", "VP_BAR_Y", "VP_BAR_H",
              "VP_RESET_Y", "VP_COLOR_CLAUDE", "VP_COLOR_CODEX"):
    assert token in source
```

Add these commands to `test/run.sh` after moving back to repository root:

```sh
python3 -m unittest tools.vibepulse_studio.test_design tools.vibepulse_studio.test_server -v
python3 tools/vibepulse_studio/design.py --check
python3 test/test_vibepulse_studio_wiring.py
```

- [ ] **Step 2: Run the host suite and verify wiring fails**

Run:

```bash
./test/run.sh
```

Expected: FAIL because `usage_screen.c` still owns duplicate constants.

- [ ] **Step 3: Replace only the matching visual constants**

Include the generated header in `usage_screen.c`. Replace provider color, screen width, safe inset, content width, provider/quota/percent/bar/reset/status coordinates and heights with the corresponding `VP_*` macros. Keep presenter logic, data semantics, missing/stale behavior, touch navigation, completion policy, and LVGL object ownership unchanged.

Do not add LVGL transforms. `plex_num_146` and the native UI fonts remain the target typography; `VP_PERCENT_FONT_PX` is a cross-check, not a runtime scaler.

- [ ] **Step 4: Export and compare both authorities**

Run Studio, export `claude.png` and `codex.png`, then run:

```bash
python3 tools/vibepulse_studio/design.py --check
./tools/preview-ui.sh vibepulse
./test/run.sh
```

Expected: every exported/reference image and simulator capture is 480 x 480. Differences caused by browser vs. LVGL font rasterization are acceptable; geometry, hierarchy, copy, bar length, and provider colors must agree.

- [ ] **Step 5: Build the target once for the approved static batch**

Run:

```bash
. ~/esp/esp-idf/export.sh
idf.py build
```

Expected: target build succeeds and size output still fits the current application partition. Record the binary size and internal-memory summary.

- [ ] **Step 6: Commit the static integration**

```bash
git add components/app_tokens/usage_screen.c test/test_vibepulse_layout_wiring.py test/run.sh design/vibepulse/exports
git commit -m "Drive VibePulse layout from Studio tokens"
```

### Task 6: Install the shared fast-loop skill and stop at the physical AMOLED gate

**Files:**
- Create: `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`
- Create: `tools/install-local-skills.sh`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Create: `docs/superpowers/reviews/2026-08-10-vibepulse-studio-static-amoled.md`

- [ ] **Step 1: Write the canonical skill**

The complete workflow in `SKILL.md` must say:

```markdown
---
name: iterating-esp32-amoled-ui
description: Use for Torget/VibePulse visual changes, exact-size mockups, simulator captures, or AMOLED review.
---

# Iterating Torget AMOLED UI

1. Read the hardware registry, UI spec, current design JSON, and latest physical review.
2. Start `python3 tools/vibepulse_studio/server.py`; work at 1:1 480 x 480 first.
3. Show the user Claude/Codex or other materially different states as soon as coherent.
4. Save approved tokens; run `design.py --check` and `tools/preview-ui.sh vibepulse`.
5. Implement one approved static batch in shared LVGL code and run `./test/run.sh`.
6. Build once, flash once, and inspect the static physical AMOLED before animation.
7. Use provider colors Claude `#D97757` and Codex `#6F78FF`; never fabricate data.
8. Do not add persistent rows, cards, rails, transforms, opacity layers, or canvas buffers without explicit approval and a measured memory budget.
```

- [ ] **Step 2: Add the Codex-link installer**

Create executable `tools/install-local-skills.sh` that resolves the repository path, creates `${CODEX_HOME:-$HOME/.codex}/skills`, refuses to overwrite a non-symlink, and links `iterating-esp32-amoled-ui` to `.claude/skills/iterating-esp32-amoled-ui`. It must print the resolved link and say that a new Codex session may be required for discovery.

- [ ] **Step 3: Add concise agent instructions**

Add to both `AGENTS.md` and `CLAUDE.md`: use the project skill for AMOLED work; show 480 x 480 output during meaningful stages; physical static review happens before motion; Studio approval never authorizes a flash.

- [ ] **Step 4: Validate and install the shared skill**

Run:

```bash
python3 /Users/niclasvestlund/.codex/skills/.system/skill-creator/scripts/quick_validate.py .claude/skills/iterating-esp32-amoled-ui
./tools/install-local-skills.sh
./test/run.sh
```

Expected: skill validation and host tests pass, and Codex's link resolves into this repository.

- [ ] **Step 5: Commit the workflow before touching the device**

```bash
git add .claude/skills/iterating-esp32-amoled-ui tools/install-local-skills.sh AGENTS.md CLAUDE.md
git commit -m "Share the AMOLED iteration workflow"
```

- [ ] **Step 6: Perform the mandatory static physical review**

Flash the approved static batch using the existing USB procedure. Photograph Claude and Codex at the physical screen's real size, inspect percentage readability at 1-2 meters, check `+N% TODAY`, reset copy, bar color/height, clipping, day/night brightness, and black-level behavior. Record firmware commit, photo paths, observations, and required corrections in `docs/superpowers/reviews/2026-08-10-vibepulse-studio-static-amoled.md`.

Stop here if the review is not explicitly accepted. Do not add or tune animation in this plan.

- [ ] **Step 7: Commit only accepted physical evidence**

```bash
git add docs/superpowers/reviews/2026-08-10-vibepulse-studio-static-amoled.md
git commit -m "Record VibePulse Studio AMOLED review"
```

## Completion gate

The preview milestone is complete when the Studio works offline at true 1:1 size, design saving regenerates a deterministic checked header, exports and simulator captures are exactly 480 x 480, the full host suite and target build pass, both agents discover the same workflow, and the static design has an accepted physical AMOLED record. Wireless flashing is deliberately absent until the separate OTA foundation passes its safety plan.
