from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "components/app_tokens/usage_screen.c").read_text()
header = (root / "components/app_tokens/usage_screen.h").read_text()
sim = (root / "sim/main.c").read_text()
monitor = (root / "components/app_tokens/agent_monitor.c").read_text()
status_font = (root / "platform/fonts/plex_status_64.c").read_text()
font_script = (root / "platform/fonts/fetch-and-convert.sh").read_text()

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
assert "create_hero_page" in source
assert '"WEEKLY · ALL MODELS"' not in source, "copy belongs in presenter"
assert '#include "vibepulse_layout.generated.h"' in source
for old in (
    "#define SCREEN_W", "#define SAFE_X", "#define CONTENT_W",
    "#define HEADER_Y", "#define HEADER_H", "#define HERO_LABEL_Y",
    "#define HERO_PCT_Y", "#define HERO_PCT_H", "#define HERO_BAR_Y",
    "#define HERO_BAR_H", "#define HERO_RESET_Y", "#define HERO_STATUS_Y",
    "#define HERO_STATUS_H",
):
    assert old not in source
for token in (
    "VP_SCREEN_W", "VP_SCREEN_H", "VP_SAFE_X", "VP_CONTENT_W",
    "VP_PROVIDER_Y", "VP_QUOTA_Y", "VP_PERCENT_Y", "VP_BAR_Y",
    "VP_BAR_H", "VP_RESET_Y", "VP_STATUS_Y", "VP_STATUS_H",
    "VP_COLOR_BACKGROUND", "VP_COLOR_TEXT", "VP_COLOR_MUTED",
    "VP_COLOR_TRACK", "VP_COLOR_HAIRLINE", "VP_COLOR_CLAUDE",
    "VP_COLOR_CODEX",
):
    assert token in source
assert "VP_PERCENT_FONT_PX == 146" in source
hero = source[source.index("static void create_hero_page"):]
hero = hero[:hero.index("static void create_summary_header")]
assert "COL_CARD" not in hero, "priority usage pages must not use cards"
assert "create_app_icon" not in hero, "hero must not spend space on app chrome"
assert "lv_obj_t *line" not in hero, "hero must not restore the removed header hairline"
assert "create_pager" not in hero, "hero must not overlay a pager on usage data"
assert "hero->today" in hero
assert "hero->status" in hero
assert "hero->status_dot" in hero
assert '"— TODAY"' not in hero, "LVGL hero font has no em-dash glyph"
assert '"– TODAY"' in hero, "missing today must use the supported en-dash glyph"
assert "quota->has_pct ? &plex_num_146 : &plex_ui_21" in source
assert 'quota->has_pct ? quota->pct_text : "–"' in source
assert 'hero->pct = text(hero->content, &plex_ui_21, COL_WHITE)' in hero
assert 'lv_label_set_text(hero->pct, "–")' in hero
assert "usage_presenter_format_agent_metadata" in source
assert "usage_presenter_data_status_text" in source
assert "create_app_icon" not in source, "VibePulse pages must not spend space on app chrome"
for forbidden_copy in (
    '"VECKOTAKT"', '"VOLYM"', '"CLAUDE IDAG · MTOK"',
    '"%d SESSIONER"', '"%.0f MTOK MÅNAD"',
):
    assert forbidden_copy not in source, f"non-English copy remains: {forbidden_copy}"
for required_copy in (
    '"BURN RATE"', '"VOLUME"', '"CLAUDE TODAY · MTOK"',
    '"%d SESSIONS"', '"%.0f MTOK MONTH"',
):
    assert required_copy in source, f"missing English copy: {required_copy}"
for forbidden_copy in ('"KLAR"', '"%u JOBBAR"'):
    assert forbidden_copy not in monitor, f"non-English completion copy remains: {forbidden_copy}"
for required_copy in ('"DONE"', '"%u WORKING"'):
    assert required_copy in monitor, f"missing English completion copy: {required_copy}"
assert '0x44' in font_script, "completion font recipe must include D for DONE"
assert '/* U+0044 "D" */' in status_font, "generated completion font must contain D"

create = source[source.index("void usage_screen_create"):]
create = create[:create.index("void usage_screen_apply_tokens")]
assert "create_pager(ui.claude.tile" not in create
assert "create_pager(ui.codex.tile" not in create

assert "provider_lane" not in monitor
assert "render_rail" not in monitor
assert "mon.rail" not in monitor
for literal in (
    "lv_obj_set_size(view->root, 480, 480)",
    "lv_obj_set_size(view->provider, 480, 24)",
    "lv_obj_set_size(view->done, 480, 76)",
):
    assert literal not in monitor, "completion overlay must use generated screen tokens"
assert "lv_obj_set_size(view->root, VP_SCREEN_W, VP_SCREEN_H)" in monitor
assert "lv_obj_set_size(view->provider, VP_SCREEN_W, 24)" in monitor
assert "lv_obj_set_size(view->done, VP_SCREEN_W, 76)" in monitor
assert "lv_obj_set_pos(hero->status, 16, status_center_y - 10)" in hero
assert 'apply_agent_file("agent-status-multi-working.json")' in sim

exports = root / "design/vibepulse/exports"
for name in ("claude-hero.png", "codex-hero.png"):
    assert (exports / name).is_file(), f"missing approved Studio export {name}"
for unsupported in ("claude.png", "codex.png"):
    assert not (exports / unsupported).exists(), f"unsupported export alias {unsupported}"

print("OK: VibePulse distance-first layout wiring")
