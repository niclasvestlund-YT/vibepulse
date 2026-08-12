#!/bin/sh
# P3: IBM Plex Sans -> LVGL-fonter med snäva glyfranger (se spec/ui-spec.md).
# TTF:erna (OFL) hämtas från IBM:s officiella repo till gitignorade src/;
# de genererade .c-filerna committas så bygget aldrig behöver node eller nät.
# Obs: google/fonts har bara variabelfonten numera (fel vikter vid konvertering),
# därför IBM-repot med statiska vikter.
set -e
cd "$(dirname "$0")"
BASE="https://github.com/IBM/plex/raw/master/packages/plex-sans/fonts/complete/ttf"

mkdir -p src
for w in Bold SemiBold Medium; do
  [ -f "src/IBMPlexSans-$w.ttf" ] || curl -fsSL "$BASE/IBMPlexSans-$w.ttf" -o "src/IBMPlexSans-$w.ttf"
done

if command -v npx >/dev/null 2>&1; then
  font_conv() { npx --yes lv_font_conv "$@"; }
elif command -v pnpm >/dev/null 2>&1; then
  font_conv() { pnpm --silent dlx lv_font_conv "$@"; }
else
  echo "font generation requires npx or pnpm" >&2
  exit 127
fi

conv() { font_conv --font "src/IBMPlexSans-$1.ttf" --size "$2" \
  --bpp 4 --format lvgl --no-compress --range "$3" -o "$4.c"; \
  python3 -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text(p.read_text().rstrip() + "\n")' "$4.c"; \
  echo "  $4.c"; }

# Sifferfonter (Bold). Ranger: 0-9, komma, mellanslag, U+00A0, %, en-dash.
conv Bold     146 "0x30-0x39,0x2C,0x20,0xA0,0x25,0x2013" plex_num_146
conv Bold     164 "0x30-0x39,0x25,0x2E,0x2013"           plex_num_164
conv Bold     118 "0x30-0x39,0x20,0x25,0x2E,0xA0,0x2013" plex_num_118
conv Bold      50 "0x30-0x39,0x25,0x2C,0x2013"           plex_num_50
# VibePulse Burn Rate outcomes: uppercase action words plus compact durations.
conv Bold      48 "0x20,0x30-0x39,0x41-0x5A"             plex_headline_48
conv Bold      35 "0x20,0x25,0x2B,0x2E,0x30-0x39,0x41-0x5A,0x2013" plex_stat_35
# Full-screen attention overlay: exact reviewed native sizes, never transforms.
conv SemiBold  18 "0x20,0x41-0x5A"                       plex_attention_18
conv SemiBold  25 "0x20,0x2D,0x2E,0x30-0x39,0x3F,0x41-0x5A,0x5F,0xC4,0xC5,0xD6" plex_attention_25
conv Bold      52 "0x20,0x41-0x5A"                       plex_attention_52
# 38 bär även gemener sedan P23: Sverige-vyns "4 aug" är ett statvärde.
conv Bold      38 "0x30-0x39,0x2C,0x20,0xA0,0x2013,0x61-0x7A" plex_num_38
# Textfonter. 32: heroenheter "kr", "%", "GWh" (P23) och "Mtok" (Tokenmätaren).
# 21/16: versaler + ÅÄÖ.
# 17: blandad text.
conv SemiBold  32 "0x25,0x47,0x4D,0x57,0x68,0x6B,0x6F,0x72,0x74" plex_text_32
conv SemiBold  27 "0x41-0x5A"                              plex_unit_27
conv SemiBold  21 "0x41-0x5A,0x20,0xC5,0xC4,0xD6"        plex_text_21
conv SemiBold  16 "0x41-0x5A,0x20,0xC5,0xC4,0xD6"        plex_text_16
conv Medium    17 "0x20,0x25,0x2C,0x30-0x39,0x41-0x5A,0x61-0x7A,0xA0,0xC5,0xC4,0xD6,0xE5,0xE4,0xF6" plex_text_17
# VibePulse-korten behöver små riktiga Plex-rader med skiljetecken, modell-
# namn och svenska tecken. De separata UI-fonterna undviker att blåsa upp de
# äldre, hårt bantade 16/17-fonterna i hela plattformen.
conv SemiBold  14 "0x20-0x7E,0xA0,0xB7,0xC5,0xC4,0xD6,0xD7,0xE5,0xE4,0xF6,0x2013" plex_ui_14
conv SemiBold  16 "0x20-0x7E,0xA0,0xB7,0xD7,0x2013,0x2248" plex_ui_16
conv SemiBold  12 "0x20-0x7E,0xA0,0xB7,0xC5,0xC4,0xD6,0xD7,0xE5,0xE4,0xF6,0x2013" plex_ui_12
# Samma kompletta UI-rang i naturliga 21 px för VibePulse. Skala aldrig
# dynamiska etiketter med LVGL-transformer: de kräver oskivbara ARGB-lager.
conv SemiBold  21 "0x20-0x7E,0xA0,0xB7,0xC5,0xC4,0xD6,0xD7,0xE5,0xE4,0xF6,0x2013" plex_ui_21
# Launcherikonerna: Vibbes P, S:et ur Solelkollens logga, äldre T och VibePulse V.
conv Bold      64 "0x50,0x53,0x54,0x56"                   plex_icon_64
# Agent monitor completion/status words: DONE plus legacy Swedish fallbacks.
conv Bold      64 "0x41,0x42,0x44-0x46,0x4A,0x4B,0x4C,0x4E,0x4F,0x52,0x54,0x56,0xC4" plex_status_64
ls -la *.c | awk '{print $5, $9}'
