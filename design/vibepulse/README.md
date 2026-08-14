# The social preview

`docs/img/social-preview.png` (1280×640) is what GitHub shows in Trending,
in search, and in every link unfurl on Threads, X, Slack and Discord.

## Uploading it — the step that actually matters

**A file in the repository does nothing on its own.** GitHub only uses an
image you upload by hand:

> Repo → **Settings** → **General** → **Social preview** → *Edit* → *Upload
> an image*

Until that upload happens, GitHub falls back to a generated card with the
repo name and the owner's avatar, which is what most trending repos look
like and what a striking banner exists to avoid. Re-upload after any
regeneration; GitHub keeps its own copy and will not notice a new commit.

Verify with the unfurl debugger of whichever platform matters, or just paste
the repo URL into a Slack DM to yourself.

## Regenerating

```sh
. .venv/bin/activate
PLEX_DIR=/path/to/plex/ttf python design/vibepulse/build_social_preview.py
```

The script composites the **real 480×480 panel rasters** from `docs/img/`,
so the banner cannot drift from what the device actually renders. Change a
screen, re-run, re-upload.

### Fonts

IBM Plex Sans, the product's own typeface, under the OFL. The repo ships it
only as LVGL `.c` arrays (`platform/fonts/`), which Pillow cannot read, so
the script needs TTFs. Either run `platform/fonts/fetch-and-convert.sh`
(which downloads them to the gitignored `platform/fonts/src/`), or convert
the npm package:

```sh
curl -sSL https://registry.npmjs.org/@ibm/plex-sans/-/plex-sans-1.1.0.tgz | tar xz
python -c "from fontTools.ttLib import TTFont; \
  f=TTFont('package/fonts/complete/woff2/IBMPlexSans-Bold.woff2'); \
  f.flavor=None; f.save('IBMPlexSans-Bold.ttf')"
```

`PLEX_DIR` must contain `IBMPlexSans-{Bold,SemiBold,Medium,Text}.ttf`.

## The design

`social-preview-philosophy.md` holds the reasoning. In short: true black so
the panels float against GitHub's dark background, exactly two
meaning-bearing accents (Claude `#D97757`, Codex `#6F78FF`), depth by scale,
blur and luminance rather than shadow, and one dominant element that stays
legible at thumbnail size — the banner is seen at roughly 400 px wide in the
mobile Trending feed far more often than at full size.

### Why the alert leads, not the quota

Both were built and compared at 420 px:

```sh
OUT=/tmp/variant-quota.png VARIANT=quota \
  python design/vibepulse/build_social_preview.py
```

The quota hero wins on raw legibility — `73%` is the most readable element
in either render, by a distance. It still lost. A large percentage is
semantically empty at a glance: 73% of what? Battery, storage, a download.
It says nothing about agents, and quota meters are everywhere.

`NEEDS YOU` is strange, human and slightly withholding, so it earns the
second look that a number does not, and the headline resolves it in one
beat. It is also the product's actual differentiator — the shoulder tap,
not the dashboard. The alert headline is better copy for the same reason:
specific where the quota line could sit on any meter.

Composition settled it. Three headline lines fill the left column to meet
the panel's height; the quota variant's two lines leave the lower left dead
and the weight too high.

### Known compromise

The hero panel is a real NEEDS YOU frame, so it shows `TORGET` — the
platform name, and the project the alert happens to be waiting on. A
stranger can read it as the product's name. Fixing it honestly means
rendering a fresh frame in the simulator with a neutral project label rather
than editing the raster, because a doctored panel render would break the
rule that every published frame is one the device can actually produce.
