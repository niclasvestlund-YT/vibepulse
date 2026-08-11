from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "components/app_tokens/usage_screen.c").read_text()
header = (root / "components/app_tokens/usage_screen.h").read_text()
app_header = (root / "components/app_tokens/app_tokens.h").read_text()
sim = (root / "sim/main.c").read_text()
monitor = (root / "components/app_tokens/agent_monitor.c").read_text()
font_script = (root / "platform/fonts/fetch-and-convert.sh").read_text()

assert "#define TK_USAGE_SCREEN_VIEWS 5" in header
for enum_literal in (
    "VIEW_CLAUDE_FABLE = 0",
    "VIEW_CLAUDE_ALL = 1",
    "VIEW_CODEX_WEEKLY = 2",
    "VIEW_BURN_RATE = 3",
    "VIEW_VOLUME = 4",
):
    assert enum_literal in app_header

for removed in (
    "create_claude_details_page",
    "create_overview_page",
    "create_card",
    "status_halo",
    "status_dot",
    "create_summary_row",
    "COL_CARD",
    "COL_BORDER",
):
    assert removed not in source, f"removed dashboard structure remains: {removed}"

for required in (
    "create_quota_page",
    "create_burn_rate_page",
    "create_volume_page",
    "usage_presenter_build_quota_page",
    "plex_num_164",
    "plex_headline_48",
    "plex_ui_16",
    "tk_img_claude_32",
    "tk_img_codex_cloud_32",
    "VP_COLOR_CLAUDE",
    "VP_COLOR_CODEX",
):
    assert required in source, f"missing full-screen UI primitive: {required}"

assert "extern const lv_font_t plex_num_146" not in source
assert "VP_PERCENT_FONT_PX == 164" in source
assert "VP_PROVIDER_Y" in source
assert "#define STAT_VALUE_Y VP_RESET_Y" in source
assert "VIEW_CLAUDE_DETAILS" not in source
assert "VIEW_OVERVIEW" not in source
assert "VIEW_CLAUDE_HERO" not in source
assert "VIEW_CODEX_HERO" not in source

create = source[source.index("void usage_screen_create"):]
create = create[:create.index("void usage_screen_apply_tokens")]
assert create.count("create_quota_page(") == 3
assert create.count("create_burn_rate_page(") == 1
assert create.count("create_volume_page(") == 1
assert "tk_agent_monitor_create(root);" in create

quota = source[source.index("static void create_quota_page"):]
quota = quota[:quota.index("static void create_burn_rate_page")]
for copy in ("USED TODAY", "TO RESET"):
    assert f'"{copy}"' in quota
assert "VP_BAR_Y" in quota and "VP_BAR_H" in quota
assert "model" in quota and "effort" in quota
assert "status" not in quota

burn = source[source.index("static void create_burn_rate_page"):]
burn = burn[:burn.index("static void create_volume_page")]
for copy in ("BURN RATE", "WEEKLY", "FORECAST"):
    assert f'"{copy}"' in burn
assert "251" in burn, "Burn Rate rows need the approved separator"
assert "COL_CARD" not in burn

volume = source[source.index("static void create_volume_page"):]
volume = volume[:volume.index("void usage_screen_create")]
for copy in ("VOLUME", "TOKENS", "USED TODAY", "SESSIONS", "MTOK THIS MONTH"):
    assert f'"{copy}"' in source
assert "COL_CARD" not in volume

assert 'lv_obj_set_tile_id(ui.tileview, index, 0, LV_ANIM_OFF)' in source
assert "lv_timer_create" not in source, "steady pages must not rotate themselves"
assert "lv_obj_set_style_opa" not in source
assert "lv_canvas" not in source
assert "lv_obj_set_style_transform" not in source

assert "conv Bold     164" in font_script
assert "plex_num_164" in font_script
assert "conv Bold      48" in font_script
assert "plex_headline_48" in font_script
assert "conv SemiBold  16" in font_script and "plex_ui_16" in font_script

assert "provider_lane" not in monitor
assert "render_rail" not in monitor
assert "mon.rail" not in monitor
assert '"DONE"' in monitor

assert "int usage_screen_current_view(void);" in header
assert "usage_screen_current_view()" in sim

print("OK: VibePulse five-page full-screen layout wiring")
