"""Build a headless native LVGL capture with an installed Zig compiler.

Usage: python build.py --zig /path/to/zig --lvgl /path/to/lvgl --output /path/to/sim.exe
Uses the same app_grok UI, fonts and asset sources as the P4 target.
"""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", required=True)
    parser.add_argument("--lvgl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    app = here.parents[2] / "components" / "app_grok"
    sources = [*args.lvgl.glob("src/**/*.c"), here / "main.c", app / "grok_ui.c",
               *app.glob("fonts/*.c"), *app.glob("assets/*.c")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    options = ["-std=c11", "-O1", "-DLV_CONF_INCLUDE_SIMPLE", "-DLV_LVGL_H_INCLUDE_SIMPLE", "-I" + str(here),
               "-I" + str(args.lvgl), "-I" + str(app), *map(str, sources),
               "-o", str(args.output)]
    response = args.output.with_suffix(".rsp")
    response.write_text("\n".join('"' + s.replace('\\', '/') + '"' for s in options))
    subprocess.run([args.zig, "cc", "@" + str(response)], check=True)


if __name__ == "__main__":
    main()
