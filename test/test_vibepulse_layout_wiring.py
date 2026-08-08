from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "components/app_tokens/usage_screen.c").read_text()

assert "extern const lv_font_t plex_num_146" in source
assert "#define HERO_BAR_H 18" in source
assert "create_hero_page" in source
assert '"WEEKLY · ALL MODELS"' not in source, "copy belongs in presenter"
assert "COL_CODEX       lv_color_hex(0x6F78FF)" in source
hero = source[source.index("static void create_hero_page"):]
hero = hero[:hero.index("static void create_forecast_page")]
assert "COL_CARD" not in hero, "priority usage pages must not use cards"

print("OK: VibePulse distance-first layout wiring")
