#!/usr/bin/env python3
"""Regression guard for LVGL's small private heap on the ESP32 target."""

from pathlib import Path


root = Path(__file__).resolve().parents[1]
usage_screen = (root / "components/app_tokens/usage_screen.c").read_text(
    encoding="utf-8"
)
agent_monitor = (root / "components/app_tokens/agent_monitor.c").read_text(
    encoding="utf-8"
)
agent_monitor_header = (
    root / "components/app_tokens/agent_monitor.h"
).read_text(encoding="utf-8")
target_main = (root / "main/main.c").read_text(encoding="utf-8")
sim_main = (root / "sim/main.c").read_text(encoding="utf-8")
platform_header = (root / "platform/torget.h").read_text(encoding="utf-8")
platform_ui = (root / "platform/torget_ui.c").read_text(encoding="utf-8")
wifi_assets_header = root / "platform/wifi_status_assets.h"
wifi_assets_source = root / "platform/wifi_status_assets.c"
font_generator = (root / "platform/fonts/fetch-and-convert.sh").read_text(
    encoding="utf-8"
)
obsolete_wifi_font = root / "platform/fonts/torget_wifi_22.c"
boot_screen = (root / "platform/boot_screen.c").read_text(encoding="utf-8")
wifi_setup_ui = (root / "components/torget_wifi/wifi_setup_ui.c").read_text(
    encoding="utf-8"
)
ota_ui = (root / "components/torget_ota/ota_ui.c").read_text(encoding="utf-8")
wifi_state_header = (root / "main/wifi_signal_state.h").read_text(encoding="utf-8")
tokens_app = (root / "components/app_tokens/app.c").read_text(encoding="utf-8")
torget_http = (root / "components/torget_net/torget_http.c").read_text(
    encoding="utf-8"
)
torget_http_header = (root / "components/torget_net/torget_http.h").read_text(
    encoding="utf-8"
)
needs_you_net = (root / "components/app_tokens/needs_you_net.c").read_text(
    encoding="utf-8"
)

# Scaling a dynamic label makes LVGL render the complete label into an
# unsliceable ARGB transform layer.  The 436 px hero labels can then request
# more contiguous memory than Torget's 96 KiB LVGL pool has available and the
# draw dispatcher spins until the task watchdog fires.
assert "lv_obj_set_style_transform_scale_" not in usage_screen, (
    "VibePulse labels must use a native-size font, never an LVGL transform layer"
)
assert "SUMMARY_LABEL_SCALE_Y" not in usage_screen
assert "extern const lv_font_t plex_ui_21;" in usage_screen
assert "extern const lv_font_t plex_num_164;" in usage_screen
assert "lv_obj_set_style_transform" not in usage_screen
assert "lv_obj_set_style_opa" not in usage_screen
assert "lv_canvas" not in usage_screen

# Needs You remains one provider-neutral LVGL tree.  Codex must only swap
# native assets/colors/copy into that tree; a second screen would double the
# persistent object and draw-memory cost that the 256 KiB pool is sized for.
assert agent_monitor.count("} needs_you_view;") == 1
assert "codex_needs_you_view" not in agent_monitor.lower()
assert "tk_img_codex_64" in agent_monitor
assert '"CODEX RECOMMENDS"' in agent_monitor
assert '"APPROVE"' in agent_monitor
assert '"ALLOW ONCE"' in agent_monitor
assert "COL_CODEX" in agent_monitor

# The approved header leaves a hard lane for the radio indicator: the
# provider/project eyebrow is ellipsized at x=148,w=260.  The Wi-Fi mark is a
# native final-size image inside the translated page shell, so it moves with
# the same burn-in drift as every page instead of floating above the screen.
# Full-screen setup/OTA overlays naturally cover it.  It reports Wi-Fi only,
# never relay health.
assert "148, 46, 260" in agent_monitor
assert "lv_label_set_long_mode(v->h_eyebrow, LV_LABEL_LONG_DOT)" in agent_monitor
assert "wifi_group" not in agent_monitor
assert "wifi_bars" not in agent_monitor
assert wifi_assets_header.exists(), "missing native Wi-Fi status asset header"
assert wifi_assets_source.exists(), "missing native Wi-Fi status asset source"
assert "tg.wifi_group = bare(tg.shift);" in platform_ui
assert "lv_obj_set_pos(tg.wifi_group, 426, 28)" in platform_ui
assert "lv_obj_set_size(tg.wifi_group, 20, 18)" in platform_ui
assert platform_ui.count("lv_image_create(tg.wifi_group)") == 1
assert "LV_SYMBOL_WIFI" not in platform_ui
assert "torget_wifi_22" not in platform_ui
assert not obsolete_wifi_font.exists()
assert "0xF1EB" not in font_generator
assert "wifi_active_clip" not in platform_ui
assert "lv_arc_create" not in platform_ui
assert "lv_obj_set_style_transform" not in platform_ui
assert "lv_line_create(tg.wifi_group)" not in platform_ui
assert "connected ? &tg_img_wifi_strong : &tg_img_wifi_offline" in platform_ui
assert "&tg_img_wifi_weak" not in platform_ui
assert "&tg_img_wifi_medium" not in platform_ui
assert "TG_WIFI_STATUS_NORMAL" in platform_header
assert "TG_WIFI_STATUS_SETUP" in platform_header
assert "TG_WIFI_STATUS_HIDDEN" in platform_header
assert "void torget_wifi_status_set_mode(tg_wifi_status_mode mode);" in platform_header
assert "void torget_wifi_status_foreground(void);" in platform_header
assert "uint8_t torget_wifi_signal_bars(void);" in platform_header
assert "Never implies relay health" in platform_header

# Setup can still force the connected state while it owns the glass, but because
# both setup and OTA are opaque top-layer overlays there is no reparenting or
# duplicate Wi-Fi object.  Boot keeps the ordinary page mark hidden until the
# one-time handoff.
wifi_foreground = wifi_setup_ui.index("lv_obj_move_foreground(ui.overlay)")
assert wifi_setup_ui.index("torget_wifi_status_foreground()", wifi_foreground) > wifi_foreground
ota_foreground = ota_ui.index("lv_obj_move_foreground(ui.overlay)")
assert ota_ui.index("torget_wifi_status_foreground()", ota_foreground) > ota_foreground
assert "torget_wifi_status_set_mode(TG_WIFI_STATUS_SETUP)" in wifi_setup_ui
assert "torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL)" in wifi_setup_ui
assert "torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL)" in ota_ui
assert "torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL)" in boot_screen

# Target sampling is lock-free and stays out of LVGL; the simulator supplies a
# deterministic fixture through the same platform API.
assert "tg_wifi_signal_state" in target_main
assert "tg_wifi_signal_event" in target_main
assert "tg_wifi_signal_sample_begin" in target_main
assert "tg_wifi_signal_sample_commit" in target_main
assert "esp_wifi_sta_get_ap_info" in target_main
assert "pdMS_TO_TICKS(5000)" in target_main
assert "lv_" not in target_main[
    target_main.index("static void wifi_signal_task"):
    target_main.index("/* ------------------------------------------------------- LVGL-tasken")
]
assert "torget_wifi_signal_bars" in sim_main
assert "sim_wifi_signal_bars" in sim_main

# One actual-font predicate owns both screen selection and verdict gating.
assert "ny_physical_fit_of" in agent_monitor
assert agent_monitor.count("ny_physical_fit_of(p") >= 3
assert "lv_font_get_glyph_dsc" in agent_monitor
assert "const int prompt_w = 300, prompt_h = 68" in agent_monitor
assert "const int title_w = 392, subtitle_w = 392" in agent_monitor
assert "const int command_w = 432, command_h = 62" in agent_monitor
assert "const int prompt_w = 346, prompt_h = 52" in agent_monitor
assert "const int command_w = 352, command_h = 48" in agent_monitor
assert "ny_text_fits(p->title, &plex_body_27, title_w, 34" in agent_monitor
assert "ny_text_fits(p->subtitle, &plex_ui_16, subtitle_w, 20" in agent_monitor
assert "#define NY_PROMPT_W 300" in agent_monitor
assert "#define NY_PROMPT_H 68" in agent_monitor
assert "#define NY_TITLE_W 392" in agent_monitor
assert "#define NY_COMMAND_W 432" in agent_monitor
assert "#define NY_COMMAND_H 62" in agent_monitor
assert "24, 182, 432" in agent_monitor
assert "payoff_provider" in agent_monitor

# The 10 Hz countdown may mutate only its visible arc.  Stable copy/provider/
# button state must not be keyed on ring_permille and fully repainted.  Target
# diagnostics expose counters only (never interaction content) and reset every
# ten-second heap sample.
assert "uint16_t ring_permille;" not in agent_monitor
assert "tk_agent_render_stats" in agent_monitor_header
assert "needs-you render: full=%u ring=%u unchanged=%u" in target_main
assert "tk_agent_monitor_render_stats_reset();" in target_main

# Connectivity becomes public only after the EventGroup and compact signal
# state agree; readers can never observe bars contradicting WIFI_GOT_IP.
disconnect = target_main[target_main.index("WIFI_EVENT_STA_DISCONNECTED"):
                         target_main.index("IP_EVENT_STA_GOT_IP")]
got_ip = target_main[target_main.index("IP_EVENT_STA_GOT_IP"):
                     target_main.index("/* Setupfönstrets krokar")]
assert disconnect.index("xEventGroupClearBits") < disconnect.index(
    "tg_wifi_signal_event")
assert got_ip.index("xEventGroupSetBits") < got_ip.index(
    "tg_wifi_signal_event")
assert "TG_WIFI_SIGNAL_TERMINAL_GENERATION" in wifi_state_header

# All Cloudflare HTTPS clients share one mutex created before app network tasks
# start. LAN status/verdict traffic never waits on it, and no source may hold
# it while entering the LVGL lock.
assert "bool torget_cloud_io_init(void);" in torget_http_header
assert "bool torget_cloud_io_acquire(uint32_t timeout_ms);" in torget_http_header
assert "void torget_cloud_io_release(void);" in torget_http_header
assert "xSemaphoreCreateMutexStatic" in torget_http
assert "torget_cloud_io_init();" in tokens_app
assert tokens_app.index("torget_cloud_io_init();") < tokens_app.index(
    "tokens_net_start();"
)
assert "torget_cloud_io_acquire" in torget_http
assert "torget_cloud_io_release" in torget_http
assert "TG_NET_LOCAL_TIMEOUT_MS,\n                          false" in torget_http
assert "source == TG_NET_SOURCE_RELAY" in torget_http
assert "TG_NET_LOCAL_TIMEOUT_MS, true" in torget_http
assert "torget_cloud_io" not in needs_you_net, (
    "the direct LAN verdict sender must never wait behind Cloudflare"
)
for network_source in (torget_http, needs_you_net):
    assert "torget_ui_lock" not in network_source
    assert "lv_" not in network_source

print("OK: VibePulse labels and shared Needs You tree stay allocation-safe")
