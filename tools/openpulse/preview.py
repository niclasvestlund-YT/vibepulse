"""Capture native shared LVGL states; sample data only. Run from repository root."""
import argparse
import json
import subprocess
from pathlib import Path
from PIL import Image
from .service import EXAMPLE, Monitor, read_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", default="sim/build-openpulse-round/openpulse-sim")
    parser.add_argument("--out", type=Path, default=Path(".openpulse/previews"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    config = read_config(EXAMPLE)
    config["account_view"] = True
    monitor = Monitor(config, demo=True, clock=lambda: 1790695800, monotonic=lambda: 1000)
    monitor.refresh("default"); monitor.refresh("management")
    normal = monitor.snapshot()
    fixtures = {
        "demo": normal,
        "normal": dict(normal, month=12.36, budgetPercent=24.72, budgetLevel="normal"),
        "critical": dict(normal, month=52.1, budgetPercent=104.2, budgetLevel="critical"),
        "stale": dict(normal, state="stale", ageSeconds=720, accountState="stale", accountAgeSeconds=720),
        "error": dict(normal, state="error", error="auth", ageSeconds=60, accountState="error"),
        "missing": dict(normal, day=None, week=None, month=None, budgetPercent=None,
                        budgetLevel="unknown", limitRemaining=None, limitState="unknown",
                        accountBalance=None, accountState="no_data", state="no_data", ageSeconds=None,
                        updatedAt=None, byokMonth=None),
        "unlimited": dict(normal, limitState="unlimited", limitRemaining=None, limitReset=None,
                          accountEnabled=False, accountState="disabled", byokMonth=2.3),
        "wide": dict(normal, name="W"*24, month=999999999.99, day=999999.99,
                     week=999999999.99, budget=1000000000, budgetPercent=99.99,
                     limitRemaining=999999999, accountBalance=-999999999, byokMonth=999999999,
                     budgetLevel="critical", ageSeconds=999999),
    }
    for name, fixture in list(fixtures.items()):
        fixtures[name + "-details"] = fixture
    for name, fixture in fixtures.items():
        path = args.out / (name + ".json")
        path.write_text(json.dumps(fixture))
        bmp = args.out / (name + ".bmp")
        bmp.unlink(missing_ok=True)
        subprocess.run([args.sim, "--fixture", str(path), "--page", "1" if name.endswith("-details") else "0", "--capture", str(bmp)], check=True)
        with Image.open(bmp) as image:
            assert image.size in ((466,466), (480,480))
            if image.size == (466,466):
                rgb=image.convert("RGB")
                for y in range(466):
                    for x in range(466):
                        if (x-232.5)**2+(y-232.5)**2>232.5**2:
                            assert max(rgb.getpixel((x,y)))<16, f"Outside circle: {name} {x},{y}"
            image.save(args.out / (name + ".png"))
    print(f"Verified {len(fixtures)} native captures in {args.out}")


if __name__ == "__main__":
    main()
