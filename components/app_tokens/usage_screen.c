#include "usage_screen.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_monitor.h"
#include "app_tokens.h"
#include "torget.h"
#include "usage_live_policy.h"
#include "usage_presenter.h"
#include "vibepulse_layout.generated.h"

extern const lv_font_t plex_num_164;
extern const lv_font_t plex_headline_48;
extern const lv_font_t plex_stat_35;
extern const lv_font_t plex_unit_27;
extern const lv_font_t plex_ui_21;
extern const lv_font_t plex_ui_16;
extern const lv_font_t plex_ui_14;
extern const lv_font_t plex_ui_12;

#define COL_BLACK     lv_color_hex(VP_COLOR_BACKGROUND)
#define COL_HAIRLINE  lv_color_hex(VP_COLOR_HAIRLINE)
#define COL_TRACK     lv_color_hex(VP_COLOR_TRACK)
#define COL_LABEL     lv_color_hex(0xB2B7C0)
#define COL_MUTED     lv_color_hex(VP_COLOR_MUTED)
#define COL_META      lv_color_hex(0xD9DCE2)
#define COL_WHITE     lv_color_hex(VP_COLOR_TEXT)
#define COL_CLAUDE    lv_color_hex(VP_COLOR_CLAUDE)
#define COL_CODEX     lv_color_hex(VP_COLOR_CODEX)
#define COL_CLAUDE_MUTED lv_color_hex(0x8A4F42)
#define COL_CODEX_MUTED  lv_color_hex(0x454B8A)
#define COL_DOT       lv_color_hex(0x41444A)
#define COL_DOT_ON    lv_color_hex(0xCDD2DA)

_Static_assert(VP_PERCENT_FONT_PX == 164,
               "plex_num_164 must match the Studio percent token");

#define HEADER_LINE_Y 63
#define PAGER_Y 456
#define STAT_VALUE_Y VP_RESET_Y
#define STAT_LABEL_Y 396
#define RIGHT_STAT_X 240
#define RIGHT_STAT_W 218

typedef struct {
  lv_obj_t *tile;
  lv_obj_t *context;
  lv_obj_t *halo;
  lv_obj_t *quota;
  lv_obj_t *percent;
  lv_obj_t *track;
  lv_obj_t *baseline_fill;
  lv_obj_t *today_fill;
  lv_obj_t *marker;
  lv_obj_t *today;
  lv_obj_t *reset;
  usage_quota_scope scope;
  usage_provider provider;
  bool has_data;
} quota_page;

typedef struct {
  lv_obj_t *root;
  lv_obj_t *label;
  lv_obj_t *headline;
  lv_obj_t *detail;
} forecast_row;

static struct {
  lv_obj_t *tileview;
  lv_obj_t *tiles[TK_USAGE_SCREEN_VIEWS];
  quota_page quotas[3];
  forecast_row forecast_rows[2];
  lv_obj_t *volume_value;
  lv_obj_t *volume_unit;
  lv_obj_t *volume_sessions;
  lv_obj_t *volume_month;
  tk_agent_snapshot agent_snapshot;
  int64_t agent_applied_at_us;
  int64_t last_now_us;
  bool has_agent_snapshot;
  bool stale;
} ui;

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *object = lv_obj_create(parent);
  lv_obj_remove_style_all(object);
  lv_obj_set_size(object, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font,
                       lv_color_t color, int x, int y, int width,
                       int height) {
  lv_obj_t *object = lv_label_create(parent);
  lv_obj_set_style_text_font(object, font, 0);
  lv_obj_set_style_text_color(object, color, 0);
  lv_obj_set_pos(object, x, y);
  lv_obj_set_size(object, width, height);
  lv_label_set_long_mode(object, LV_LABEL_LONG_CLIP);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static void open_launcher(lv_event_t *event) {
  (void)event;
  torget_launcher_open();
}

static lv_obj_t *create_codex_icon(lv_obj_t *parent, int x, int y) {
  lv_obj_t *group = bare(parent);
  lv_obj_set_pos(group, x, y);
  lv_obj_set_size(group, 32, 32);
  const lv_image_dsc_t *layers[] = {
      &tk_img_codex_cloud_32,
      &tk_img_codex_chevron_32,
      &tk_img_codex_underscore_32,
  };
  for (size_t i = 0; i < sizeof layers / sizeof layers[0]; i++) {
    lv_obj_t *image = lv_image_create(group);
    lv_image_set_src(image, layers[i]);
    lv_obj_remove_flag(image, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(image, LV_ALIGN_CENTER, 0, 0);
    if (i > 0) {
      lv_obj_set_style_image_recolor(image, COL_WHITE, 0);
      lv_obj_set_style_image_recolor_opa(image, LV_OPA_COVER, 0);
    }
  }
  return group;
}

static void create_claude_icon(lv_obj_t *parent, int x, int y) {
  lv_obj_t *image = lv_image_create(parent);
  lv_image_set_src(image, &tk_img_claude_32);
  lv_obj_set_pos(image, x, y - 2);
  lv_obj_set_style_image_recolor(image, COL_CLAUDE, 0);
  lv_obj_set_style_image_recolor_opa(image, LV_OPA_COVER, 0);
  lv_obj_remove_flag(image, LV_OBJ_FLAG_CLICKABLE);
}

static void create_hairline(lv_obj_t *parent, int y) {
  lv_obj_t *line = bare(parent);
  lv_obj_set_pos(line, VP_SAFE_X, y);
  lv_obj_set_size(line, VP_CONTENT_W, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_HAIRLINE, 0);
}

static void create_provider_identity(lv_obj_t *tile,
                                     usage_provider provider) {
  if (provider == USAGE_PROVIDER_CLAUDE)
    create_claude_icon(tile, VP_SAFE_X, VP_PROVIDER_Y - 2);
  else
    create_codex_icon(tile, VP_SAFE_X, VP_PROVIDER_Y - 2);

  lv_obj_t *provider_name = label(tile, &plex_ui_21, COL_WHITE,
                                  64, VP_PROVIDER_Y + 1, 180, 30);
  lv_obj_set_style_text_letter_space(provider_name, 2, 0);
  lv_label_set_text(provider_name,
                    provider == USAGE_PROVIDER_CLAUDE ? "CLAUDE" : "CODEX");
}

static void create_quota_header(quota_page *page) {
  page->halo = bare(page->tile);
  lv_obj_set_pos(page->halo, 18, 14);
  lv_obj_set_size(page->halo, 40, 40);
  lv_obj_set_style_radius(page->halo, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_border_width(page->halo, 2, 0);
  lv_obj_set_style_border_opa(page->halo, LV_OPA_COVER, 0);
  lv_obj_set_style_border_color(
      page->halo,
      page->provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX, 0);
  lv_obj_add_flag(page->halo, LV_OBJ_FLAG_HIDDEN);

  create_provider_identity(page->tile, page->provider);
  page->context = label(page->tile, &plex_ui_14, COL_META,
                        180, VP_PROVIDER_Y + 5, 278, 20);
  lv_obj_set_style_text_align(page->context, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(page->context, 1, 0);
  create_hairline(page->tile, HEADER_LINE_Y);
}

static void create_volume_header(lv_obj_t *tile) {
  create_provider_identity(tile, USAGE_PROVIDER_CLAUDE);
  lv_obj_t *top = label(tile, &plex_ui_14, COL_META,
                        258, VP_PROVIDER_Y - 1, 200, 18);
  lv_obj_set_style_text_align(top, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(top, 1, 0);
  lv_label_set_text(top, "VOLUME");
  lv_obj_t *bottom = label(tile, &plex_ui_12, COL_MUTED,
                           258, VP_PROVIDER_Y + 20, 200, 16);
  lv_obj_set_style_text_align(bottom, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(bottom, 2, 0);
  lv_label_set_text(bottom, "TOKENS");
  create_hairline(tile, HEADER_LINE_Y);
}

static void create_analytics_header(lv_obj_t *tile, const char *title,
                                    const char *top_right,
                                    const char *bottom_right) {
  lv_obj_t *heading = label(tile, &plex_ui_21, COL_WHITE,
                            VP_SAFE_X, 23, 240, 30);
  lv_obj_set_style_text_letter_space(heading, 2, 0);
  lv_label_set_text(heading, title);
  lv_obj_t *top = label(tile, &plex_ui_14, COL_META, 280, 21, 178, 18);
  lv_obj_set_style_text_align(top, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(top, 1, 0);
  lv_label_set_text(top, top_right);
  lv_obj_t *bottom = label(tile, &plex_ui_12, COL_MUTED,
                           280, 42, 178, 16);
  lv_obj_set_style_text_align(bottom, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(bottom, 2, 0);
  lv_label_set_text(bottom, bottom_right);
  create_hairline(tile, HEADER_LINE_Y);
}

static void create_pager(lv_obj_t *tile, int active) {
  int x = 209;
  for (int i = 0; i < TK_USAGE_SCREEN_VIEWS; i++) {
    int width = i == active ? 18 : 6;
    lv_obj_t *dot = bare(tile);
    lv_obj_set_pos(dot, x, PAGER_Y);
    lv_obj_set_size(dot, width, 6);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(dot, i == active ? COL_DOT_ON : COL_DOT, 0);
    x += width + 5;
  }
}

static lv_obj_t *new_tile(int index) {
  lv_dir_t direction = index == 0 ? LV_DIR_RIGHT :
                       index == TK_USAGE_SCREEN_VIEWS - 1 ? LV_DIR_LEFT :
                                                           LV_DIR_HOR;
  lv_obj_t *tile = lv_tileview_add_tile(ui.tileview, index, 0, direction);
  lv_obj_remove_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_style_bg_opa(tile, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(tile, COL_BLACK, 0);
  lv_obj_add_event_cb(tile, open_launcher, LV_EVENT_LONG_PRESSED, NULL);
  ui.tiles[index] = tile;
  return tile;
}

static void create_stat(lv_obj_t *tile, lv_obj_t **value_out,
                        int x, int width, bool right, lv_color_t color,
                        const char *caption) {
  *value_out = label(tile, &plex_stat_35, color, x, STAT_VALUE_Y, width, 42);
  lv_obj_set_style_text_align(*value_out,
                              right ? LV_TEXT_ALIGN_RIGHT : LV_TEXT_ALIGN_LEFT,
                              0);
  lv_obj_t *name = label(tile, &plex_ui_14, COL_MUTED,
                         x, STAT_LABEL_Y, width, 20);
  lv_obj_set_style_text_align(name,
                              right ? LV_TEXT_ALIGN_RIGHT : LV_TEXT_ALIGN_LEFT,
                              0);
  lv_obj_set_style_text_letter_space(name, 2, 0);
  lv_label_set_text(name, caption);
}

static void create_quota_page(quota_page *page, int index,
                              usage_quota_scope scope,
                              usage_provider provider) {
  memset(page, 0, sizeof *page);
  page->scope = scope;
  page->provider = provider;
  page->tile = new_tile(index);
  create_quota_header(page);

  page->quota = label(page->tile, &plex_ui_21, COL_LABEL,
                      VP_SAFE_X, VP_QUOTA_Y, VP_CONTENT_W, 30);
  lv_obj_set_style_text_letter_space(page->quota, 2, 0);

  page->percent = label(page->tile, &plex_num_164, COL_WHITE,
                        16, VP_PERCENT_Y, 448, 190);
  lv_obj_set_style_text_letter_space(page->percent, -9, 0);
  lv_label_set_text(page->percent, "–");

  page->track = bare(page->tile);
  lv_obj_set_pos(page->track, VP_SAFE_X, VP_BAR_Y);
  lv_obj_set_size(page->track, VP_CONTENT_W, VP_BAR_H);
  lv_obj_set_style_bg_opa(page->track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(page->track, COL_TRACK, 0);
  lv_obj_set_style_radius(page->track, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_clip_corner(page->track, true, 0);

  page->baseline_fill = bare(page->track);
  lv_obj_set_pos(page->baseline_fill, 0, 0);
  lv_obj_set_size(page->baseline_fill, 0, VP_BAR_H);
  lv_obj_set_style_bg_opa(page->baseline_fill, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(
      page->baseline_fill,
      provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE_MUTED : COL_CODEX_MUTED,
      0);

  page->today_fill = bare(page->track);
  lv_obj_set_pos(page->today_fill, 0, 0);
  lv_obj_set_size(page->today_fill, 0, VP_BAR_H);
  lv_obj_set_style_bg_opa(page->today_fill, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(
      page->today_fill,
      provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX, 0);

  page->marker = bare(page->tile);
  lv_obj_set_pos(page->marker, VP_SAFE_X, VP_BAR_Y - 4);
  lv_obj_set_size(page->marker, 3, VP_BAR_H + 8);
  lv_obj_set_style_bg_opa(page->marker, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(page->marker, COL_WHITE, 0);
  lv_obj_add_flag(page->marker, LV_OBJ_FLAG_HIDDEN);

  create_stat(page->tile, &page->today, VP_SAFE_X, 210, false,
              provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX,
              "USED TODAY");
  create_stat(page->tile, &page->reset, RIGHT_STAT_X, RIGHT_STAT_W, true,
              COL_WHITE, "TO RESET");
  lv_label_set_text(page->today, "–");
  lv_label_set_text(page->reset, "–");
  create_pager(page->tile, index);
}

static void create_forecast_row(lv_obj_t *tile, forecast_row *row,
                                int y, usage_provider provider) {
  row->root = bare(tile);
  lv_obj_set_pos(row->root, VP_SAFE_X, y);
  lv_obj_set_size(row->root, VP_CONTENT_W, 120);
  row->label = label(row->root, &plex_ui_16, COL_LABEL, 0, 0,
                     VP_CONTENT_W, 22);
  lv_obj_set_style_text_letter_space(row->label, 2, 0);
  row->headline = label(row->root, &plex_headline_48, COL_WHITE,
                        0, 31, VP_CONTENT_W, 58);
  lv_obj_set_style_text_letter_space(row->headline, -2, 0);
  row->detail = label(row->root, &plex_ui_16,
                      provider == USAGE_PROVIDER_CLAUDE
                          ? COL_CLAUDE : COL_CODEX,
                      0, 95, VP_CONTENT_W, 24);
  lv_obj_set_style_text_letter_space(row->detail, 1, 0);
}

static void create_burn_rate_page(void) {
  lv_obj_t *tile = new_tile(VIEW_BURN_RATE);
  create_analytics_header(tile, "BURN RATE", "WEEKLY", "FORECAST");
  create_forecast_row(tile, &ui.forecast_rows[0], 82,
                      USAGE_PROVIDER_CLAUDE);
  create_hairline(tile, 251);
  create_forecast_row(tile, &ui.forecast_rows[1], 270,
                      USAGE_PROVIDER_CODEX);
  create_pager(tile, VIEW_BURN_RATE);
}

static void create_volume_page(void) {
  lv_obj_t *tile = new_tile(VIEW_VOLUME);
  create_volume_header(tile);

  lv_obj_t *caption = label(tile, &plex_ui_21, COL_LABEL,
                            VP_SAFE_X, VP_QUOTA_Y, VP_CONTENT_W, 30);
  lv_obj_set_style_text_letter_space(caption, 2, 0);
  lv_label_set_text(caption, "USED TODAY");

  ui.volume_value = label(tile, &plex_num_164, COL_WHITE,
                          18, 105, 330, 188);
  lv_obj_set_style_text_letter_space(ui.volume_value, -8, 0);
  lv_label_set_text(ui.volume_value, "–");
  ui.volume_unit = label(tile, &plex_unit_27, COL_MUTED,
                         340, 235, 118, 38);
  lv_obj_set_style_text_letter_space(ui.volume_unit, 1, 0);
  lv_label_set_text(ui.volume_unit, "MTOK");
  create_hairline(tile, 304);
  create_stat(tile, &ui.volume_sessions, VP_SAFE_X, 210, false,
              COL_WHITE, "SESSIONS");
  create_stat(tile, &ui.volume_month, RIGHT_STAT_X, RIGHT_STAT_W, true,
              COL_WHITE, "MTOK THIS MONTH");
  lv_label_set_text(ui.volume_sessions, "–");
  lv_label_set_text(ui.volume_month, "–");
  create_pager(tile, VIEW_VOLUME);
}

static uint64_t agent_packet_age_ms(int64_t now_us) {
  if (!ui.has_agent_snapshot || now_us <= ui.agent_applied_at_us) {
    return 0;
  }
  return ((uint64_t)now_us - (uint64_t)ui.agent_applied_at_us) / 1000ULL;
}

static const tk_agent_provider_status *agent_provider_for(
    usage_provider provider) {
  return provider == USAGE_PROVIDER_CLAUDE ? &ui.agent_snapshot.claude
                                           : &ui.agent_snapshot.codex;
}

static void refresh_header(quota_page *page, int64_t now_us) {
  usage_live_header_view view = {0};
  usage_live_build_header(agent_provider_for(page->provider),
                          agent_packet_age_ms(now_us), ui.stale,
                          ui.has_agent_snapshot, &view);

  if (!page->has_data) {
    lv_label_set_text(page->context, "NO DATA");
  } else if (ui.stale) {
    lv_label_set_text(page->context, "STALE");
  } else {
    lv_label_set_text(page->context, view.context);
  }

  if (page->has_data && !ui.stale && view.halo_active) {
    lv_obj_remove_flag(page->halo, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(page->halo, LV_OBJ_FLAG_HIDDEN);
  }
}

static void apply_today_bar(quota_page *page,
                            const usage_card_view *quota) {
  usage_today_bar_view bar = {0};
  bool available = usage_live_build_today_bar(
      quota->pct, quota->has_pct, quota->delta_pct, quota->has_delta,
      VP_CONTENT_W, &bar);

  lv_obj_set_x(page->baseline_fill, 0);
  lv_obj_set_width(page->baseline_fill,
                   available && bar.has_today ? bar.baseline_px : 0);
  lv_obj_set_x(page->today_fill,
               available && bar.has_today ? bar.baseline_px : 0);
  lv_obj_set_width(page->today_fill,
                   !available ? 0 : bar.has_today ? bar.today_px
                                                  : bar.total_px);

  if (!available || !bar.has_today) {
    lv_obj_add_flag(page->marker, LV_OBJ_FLAG_HIDDEN);
    return;
  }

  int marker_x = VP_SAFE_X + bar.marker_x - 1;
  if (marker_x < VP_SAFE_X) marker_x = VP_SAFE_X;
  if (marker_x > VP_SAFE_X + VP_CONTENT_W - 3)
    marker_x = VP_SAFE_X + VP_CONTENT_W - 3;
  lv_obj_set_x(page->marker, marker_x);
  lv_obj_remove_flag(page->marker, LV_OBJ_FLAG_HIDDEN);
}

static void apply_quota(quota_page *page, const tk_tokens *tokens) {
  usage_quota_page_view view = {0};
  usage_presenter_build_quota_page(tokens, page->scope, &view);
  const usage_card_view *quota = &view.quota;
  page->has_data = quota->has_pct;
  lv_label_set_text(page->quota, quota->label);
  lv_label_set_text(page->percent,
                    quota->has_pct ? quota->pct_text : "");
  lv_label_set_text(page->today,
                    quota->has_delta ? quota->delta_text : "–");
  lv_label_set_text(page->reset, quota->reset_short_text);
  apply_today_bar(page, quota);
  refresh_header(page, ui.last_now_us);
}

static void apply_forecast_row(forecast_row *widgets,
                               const usage_forecast_row_view *view) {
  if (!view->visible) {
    lv_obj_add_flag(widgets->root, LV_OBJ_FLAG_HIDDEN);
    return;
  }
  lv_obj_remove_flag(widgets->root, LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text(widgets->label, view->label);
  lv_label_set_text(widgets->headline, view->headline);
  lv_label_set_text(widgets->detail, view->detail);
}

void usage_screen_create(lv_obj_t *root) {
  memset(&ui, 0, sizeof ui);
  ui.tileview = lv_tileview_create(root);
  lv_obj_set_size(ui.tileview, VP_SCREEN_W, VP_SCREEN_H);
  lv_obj_set_scrollbar_mode(ui.tileview, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_opa(ui.tileview, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(ui.tileview, COL_BLACK, 0);

  create_quota_page(&ui.quotas[0], VIEW_CLAUDE_FABLE,
                    USAGE_QUOTA_CLAUDE_MODEL, USAGE_PROVIDER_CLAUDE);
  create_quota_page(&ui.quotas[1], VIEW_CLAUDE_ALL,
                    USAGE_QUOTA_CLAUDE_ALL, USAGE_PROVIDER_CLAUDE);
  create_quota_page(&ui.quotas[2], VIEW_CODEX_WEEKLY,
                    USAGE_QUOTA_CODEX_WEEK, USAGE_PROVIDER_CODEX);
  create_burn_rate_page();
  create_volume_page();
  tk_agent_monitor_create(root);
}

void usage_screen_apply_tokens(const tk_tokens *tokens) {
  if (!tokens) return;
  for (int i = 0; i < 3; i++) apply_quota(&ui.quotas[i], tokens);
  usage_forecast_page_view forecasts = {0};
  usage_presenter_build_forecasts(tokens, &forecasts);
  for (int i = 0; i < 2; i++)
    apply_forecast_row(&ui.forecast_rows[i], &forecasts.rows[i]);
}

void usage_screen_apply_agent(const tk_agent_snapshot *snapshot,
                              int64_t now_us) {
  if (!snapshot) return;
  ui.agent_snapshot = *snapshot;
  ui.agent_applied_at_us = now_us;
  ui.last_now_us = now_us;
  ui.has_agent_snapshot = true;
  for (int i = 0; i < 3; i++) refresh_header(&ui.quotas[i], now_us);
  tk_agent_monitor_apply(snapshot, now_us);
}

void usage_screen_tick(int64_t now_us) {
  ui.last_now_us = now_us;
  for (int i = 0; i < 3; i++) refresh_header(&ui.quotas[i], now_us);
  tk_agent_monitor_tick(now_us);
}

void usage_screen_set_volume(double day_mtok, int sessions,
                             double month_mtok) {
  char buffer[48];
  if (isfinite(day_mtok)) {
    snprintf(buffer, sizeof buffer, "%.0f", day_mtok);
    lv_label_set_text(ui.volume_value, buffer);
  }
  snprintf(buffer, sizeof buffer, "%d", sessions);
  lv_label_set_text(ui.volume_sessions, buffer);
  snprintf(buffer, sizeof buffer, "%.0f", month_mtok);
  lv_label_set_text(ui.volume_month, buffer);
}

void usage_screen_set_stale(bool stale) {
  ui.stale = stale;
  for (int i = 0; i < 3; i++)
    refresh_header(&ui.quotas[i], ui.last_now_us);
}

void usage_screen_show_view(int index) {
  if (index < 0) index = 0;
  if (index >= TK_USAGE_SCREEN_VIEWS) index = TK_USAGE_SCREEN_VIEWS - 1;
  lv_obj_set_tile_id(ui.tileview, index, 0, LV_ANIM_OFF);
}

int usage_screen_current_view(void) {
  lv_obj_t *active = lv_tileview_get_tile_active(ui.tileview);
  for (int i = 0; i < TK_USAGE_SCREEN_VIEWS; i++) {
    if (ui.tiles[i] == active) return i;
  }
  return VIEW_CLAUDE_FABLE;
}
