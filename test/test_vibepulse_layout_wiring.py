from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "components/app_tokens/usage_screen.c").read_text()
header = (root / "components/app_tokens/usage_screen.h").read_text()
sim = (root / "sim/main.c").read_text()

assert "#define TK_USAGE_SCREEN_VIEWS 6" in header
assert "create_claude_details_page" in source
assert "create_overview_page" in source
assert "set_summary_label" in source
assert "lv_label_set_long_mode(row->label, LV_LABEL_LONG_CLIP)" in source
assert "extern const lv_font_t plex_ui_21" in source
assert "text(parent, &plex_ui_21, color)" in source
assert "#define SUMMARY_LABEL_W 190" in source
assert "create_ui21_label" in source
assert "hero->quota = create_ui21_label" in source
assert "hero->reset = create_ui21_label" in source
assert "hero->quota = text(hero->content, &plex_text_21" not in source
assert "hero->reset = text(hero->content, &plex_text_21" not in source
assert "int usage_screen_current_view(void);" in header
assert "usage_screen_current_view()" in sim
for tag in (
    "vibepulse-claude-hero",
    "vibepulse-codex-hero",
    "vibepulse-claude-details",
    "vibepulse-overview",
):
    assert tag in sim

for provider in ("claude", "codex"):
    stale = sim.index(f'vibepulse-{provider}-hero-stale')
    missing = sim.index(f'vibepulse-{provider}-hero-missing')
    assert stale < missing, "stale capture must retain normal quota before missing data"

static_qa = sim[sim.index("static int run_vibepulse_static_qa"):
                sim.index("static void run_vibepulse_completion_qa")]
assert static_qa.count("feed_tokens();") == 1
main = sim[sim.index("int main("):]
assert main.index('"--vibepulse-static-qa"') < main.index("feed_tokens();")

assert "extern const lv_font_t plex_num_146" in source
assert "#define HERO_BAR_H 18" in source
assert "create_hero_page" in source
assert '"WEEKLY · ALL MODELS"' not in source, "copy belongs in presenter"
assert "COL_CODEX       lv_color_hex(0x6F78FF)" in source
hero = source[source.index("static void create_hero_page"):]
hero = hero[:hero.index("static void create_forecast_page")]
assert "COL_CARD" not in hero, "priority usage pages must not use cards"

print("OK: VibePulse distance-first layout wiring")
