#include "usage_screen.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_monitor.h"
#include "app_tokens.h"
#include "usage_presenter.h"
#include "torget.h"

extern const lv_font_t plex_num_146;
extern const lv_font_t plex_num_118;
extern const lv_font_t plex_num_50;
extern const lv_font_t plex_text_21;
extern const lv_font_t plex_text_32;
extern const lv_font_t plex_text_17;
extern const lv_font_t plex_text_16;
extern const lv_font_t plex_ui_14;
extern const lv_font_t plex_ui_12;

#define COL_BLACK       lv_color_hex(0x000000)
#define COL_CARD        lv_color_hex(0x17191C)
#define COL_BORDER      lv_color_hex(0x282B30)
#define COL_HAIRLINE    lv_color_hex(0x202328)
#define COL_TRACK       lv_color_hex(0x303238)
#define COL_LABEL       lv_color_hex(0xABB1BA)
#define COL_MUTED       lv_color_hex(0x858C97)
#define COL_RESET       lv_color_hex(0xD4D8DF)
#define COL_WHITE       lv_color_hex(0xFFFFFF)
#define COL_CLAUDE      lv_color_hex(0xD97757)
#define COL_CODEX       lv_color_hex(0x6F78FF)
#define COL_APP_PLATE   lv_color_hex(0x181636)
#define COL_APP_DOT     lv_color_hex(0x7770FF)
#define COL_DOT         lv_color_hex(0x41444A)
#define COL_DOT_ON      lv_color_hex(0xCDD2DA)
#define COL_STALE       lv_color_hex(0x5C687B)

#define SCREEN_W 480
#define SAFE_X 22
#define CONTENT_W 436
#define HEADER_Y 16
#define HEADER_H 48
#define HERO_LABEL_Y 86
#define HERO_PCT_Y 118
#define HERO_PCT_H 132
#define HERO_BAR_Y 276
#define HERO_BAR_H 18
#define HERO_RESET_Y 312
#define HERO_STATUS_Y 388
#define HERO_STATUS_H 66

#define SUMMARY_ROW_1_Y 78
#define SUMMARY_ROW_2_Y 274
#define SUMMARY_TRACK_Y 136
#define SUMMARY_RESET_Y 160
#define SUMMARY_PCT_X 150
#define SUMMARY_PCT_W (CONTENT_W - SUMMARY_PCT_X)
#define SUMMARY_LABEL_SCALE_Y 384 /* 14 px glyph set rendered at 21 px. */

#define LEGACY_CONTENT_X 18
#define LEGACY_CONTENT_W 444
#define LEGACY_HEADER_Y 15
#define LEGACY_HEADER_H 52
#define CARD_H 131
#define CARD_1_Y 76
#define CARD_2_Y 216
#define FOOTER_Y 366
#define FOOTER_H 66

typedef struct {
  lv_obj_t *root;
  lv_obj_t *label;
  lv_obj_t *delta;
  lv_obj_t *pct;
  lv_obj_t *pct_suffix;
  lv_obj_t *reset_short;
  lv_obj_t *before_fill;
  lv_obj_t *today_fill;
  lv_obj_t *reset;
  int track_w;
} usage_card_widgets;

typedef struct {
  lv_obj_t *tile;
  lv_obj_t *content;
  lv_obj_t *provider;
  lv_obj_t *model;
  lv_obj_t *quota;
  lv_obj_t *pct;
  lv_obj_t *track;
  lv_obj_t *fill;
  lv_obj_t *reset;
} hero_widgets;

typedef struct {
  lv_obj_t *tile;
  hero_widgets hero;
} provider_page;

typedef struct {
  lv_obj_t *provider_icon;
  lv_obj_t *provider;
  lv_obj_t *label;
  lv_obj_t *pct;
  lv_obj_t *used;
  lv_obj_t *track;
  lv_obj_t *fill;
  lv_obj_t *reset;
  lv_obj_t *status_dot;
} summary_row_widgets;

typedef struct {
  lv_obj_t *tile;
  lv_obj_t *content;
  summary_row_widgets rows[2];
} summary_page;

static struct {
  lv_obj_t *tileview;
  lv_obj_t *tiles[TK_USAGE_SCREEN_VIEWS];
  lv_obj_t *dots[TK_USAGE_SCREEN_VIEWS];
  provider_page claude;
  provider_page codex;
  summary_page claude_details;
  summary_page overview;
  usage_card_widgets forecast_cards[2];
  lv_obj_t *volume_value;
  lv_obj_t *volume_sessions;
  lv_obj_t *volume_month;
  bool stale;
} ui;

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *object = lv_obj_create(parent);
  lv_obj_remove_style_all(object);
  lv_obj_set_size(object, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static lv_obj_t *text(lv_obj_t *parent, const lv_font_t *font,
                      lv_color_t color) {
  lv_obj_t *object = lv_label_create(parent);
  lv_obj_set_style_text_font(object, font, 0);
  lv_obj_set_style_text_color(object, color, 0);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static void open_launcher(lv_event_t *event) {
  (void)event;
  torget_launcher_open();
}

static void create_app_icon(lv_obj_t *parent, int x, int y) {
  lv_obj_t *plate = bare(parent);
  lv_obj_set_pos(plate, x, y);
  lv_obj_set_size(plate, 34, 34);
  lv_obj_set_style_bg_opa(plate, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(plate, COL_APP_PLATE, 0);
  lv_obj_set_style_radius(plate, 9, 0);

  lv_obj_t *v = lv_label_create(plate);
  lv_obj_set_style_text_font(v, &plex_text_21, 0);
  lv_obj_set_style_text_color(v, COL_WHITE, 0);
  lv_obj_set_style_text_letter_space(v, -1, 0);
  lv_label_set_text(v, "V");
  lv_obj_set_pos(v, 8, 5);

  lv_obj_t *dot = bare(plate);
  lv_obj_set_pos(dot, 25, 24);
  lv_obj_set_size(dot, 5, 5);
  lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(dot, COL_APP_DOT, 0);
}

static lv_obj_t *create_codex_icon_32(lv_obj_t *parent, int x, int y) {
  lv_obj_t *group = bare(parent);
  lv_obj_set_pos(group, x, y);
  lv_obj_set_size(group, 32, 32);

  const lv_image_dsc_t *layers[3] = {
    &tk_img_codex_cloud_32,
    &tk_img_codex_chevron_32,
    &tk_img_codex_underscore_32,
  };
  for (int i = 0; i < 3; i++) {
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

static void create_header(lv_obj_t *tile, const char *provider) {
  create_app_icon(tile, LEGACY_CONTENT_X, LEGACY_HEADER_Y + 9);
  lv_obj_t *label = text(tile, &plex_text_21, COL_WHITE);
  lv_obj_set_pos(label, 63, LEGACY_HEADER_Y + 15);
  lv_obj_set_width(label, 190);
  lv_obj_set_style_text_letter_space(label, 2, 0);
  lv_label_set_text(label, provider);

  lv_obj_t *line = bare(tile);
  lv_obj_set_pos(line, LEGACY_CONTENT_X, LEGACY_HEADER_Y + LEGACY_HEADER_H - 1);
  lv_obj_set_size(line, LEGACY_CONTENT_W, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_HAIRLINE, 0);
}

static void create_card(lv_obj_t *tile, usage_card_widgets *card, int y,
                        int height, bool large) {
  card->root = bare(tile);
  lv_obj_set_pos(card->root, LEGACY_CONTENT_X, y);
  lv_obj_set_size(card->root, LEGACY_CONTENT_W, height);
  lv_obj_set_style_bg_opa(card->root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(card->root, COL_CARD, 0);
  lv_obj_set_style_border_width(card->root, 1, 0);
  lv_obj_set_style_border_color(card->root, COL_BORDER, 0);
  lv_obj_set_style_radius(card->root, 12, 0);

  card->label = lv_label_create(card->root);
  lv_obj_set_style_text_font(card->label, &plex_ui_14, 0);
  lv_obj_set_style_text_color(card->label, COL_LABEL, 0);
  lv_obj_set_style_text_letter_space(card->label, 2, 0);
  lv_obj_set_pos(card->label, 14, large ? 18 : 10);
  lv_obj_set_size(card->label, 250, 20);
  lv_label_set_text(card->label, "");

  card->delta = lv_label_create(card->root);
  lv_obj_set_style_text_font(card->delta, &plex_ui_12, 0);
  lv_obj_set_style_text_color(card->delta, COL_CLAUDE, 0);
  lv_obj_set_style_text_align(card->delta, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(card->delta, 1, 0);
  lv_obj_set_pos(card->delta, 260, large ? 18 : 10);
  lv_obj_set_size(card->delta, 170, 20);
  lv_label_set_text(card->delta, "");

  card->pct = text(card->root, large ? &plex_num_118 : &plex_num_50,
                   COL_WHITE);
  lv_obj_set_pos(card->pct, 14, large ? 48 : 34);
  lv_obj_set_size(card->pct, large ? 416 : 170, large ? 124 : 58);
  lv_label_set_long_mode(card->pct, LV_LABEL_LONG_CLIP);
  lv_label_set_text(card->pct, "–");

  card->pct_suffix = text(card->root,
                          large ? &plex_text_32 : &plex_text_21, COL_WHITE);
  lv_obj_set_pos(card->pct_suffix, large ? 220 : 112,
                 large ? 118 : 59);
  lv_label_set_text(card->pct_suffix, "%");
  lv_obj_add_flag(card->pct_suffix, LV_OBJ_FLAG_HIDDEN);

  card->reset_short = lv_label_create(card->root);
  lv_obj_set_style_text_font(card->reset_short, &plex_ui_12, 0);
  lv_obj_set_style_text_color(card->reset_short, COL_MUTED, 0);
  lv_obj_set_style_text_align(card->reset_short, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_pos(card->reset_short, 235, large ? 116 : 52);
  lv_obj_set_size(card->reset_short, 195, 20);
  lv_label_set_text(card->reset_short, "");

  card->track_w = LEGACY_CONTENT_W - 28;
  lv_obj_t *track = bare(card->root);
  lv_obj_set_pos(track, 14, large ? 166 : 91);
  lv_obj_set_size(track, card->track_w, 9);
  lv_obj_set_style_radius(track, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(track, COL_TRACK, 0);
  lv_obj_set_style_clip_corner(track, true, 0);

  card->before_fill = bare(track);
  lv_obj_set_pos(card->before_fill, 0, 0);
  lv_obj_set_size(card->before_fill, 0, 9);
  lv_obj_set_style_bg_opa(card->before_fill, LV_OPA_COVER, 0);
  card->today_fill = bare(track);
  lv_obj_set_pos(card->today_fill, 0, 0);
  lv_obj_set_size(card->today_fill, 0, 9);
  lv_obj_set_style_bg_opa(card->today_fill, LV_OPA_COVER, 0);

  card->reset = lv_label_create(card->root);
  lv_obj_set_style_text_font(card->reset, &plex_ui_14, 0);
  lv_obj_set_style_text_color(card->reset, COL_RESET, 0);
  lv_obj_set_style_text_letter_space(card->reset, 1, 0);
  lv_obj_set_pos(card->reset, 14, large ? 190 : 108);
  lv_obj_set_size(card->reset, 410, 20);
  lv_label_set_text(card->reset, "");
}

static void create_pager(lv_obj_t *tile, int active) {
  lv_obj_t *box = bare(tile);
  lv_obj_set_pos(box, 205, 354);
  lv_obj_set_size(box, 70, 16);
  lv_obj_set_flex_flow(box, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(box, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(box, 5, 0);
  for (int i = 0; i < TK_USAGE_SCREEN_VIEWS; i++) {
    lv_obj_t *dot = bare(box);
    lv_obj_set_size(dot, i == active ? 13 : 5, 5);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(dot, i == active ? COL_DOT_ON : COL_DOT, 0);
    ui.dots[i] = dot;
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

static void apply_hero(hero_widgets *widgets, const usage_hero_view *view,
                       lv_color_t provider_color) {
  const usage_card_view *quota = &view->quota;
  lv_label_set_text(widgets->provider, view->provider_label);
  lv_label_set_text(widgets->quota, quota->label);
  lv_label_set_text(widgets->pct, quota->has_pct ? quota->pct_text : "–");
  lv_label_set_text(widgets->reset, quota->reset_text);

  double pct = quota->has_pct ? quota->pct : 0.0;
  if (pct < 0.0) pct = 0.0;
  if (pct > 100.0) pct = 100.0;
  int fill_w = (int)llround(pct * CONTENT_W / 100.0);
  if (fill_w < 0) fill_w = 0;
  if (fill_w > CONTENT_W) fill_w = CONTENT_W;
  lv_obj_set_width(widgets->fill, fill_w);
  lv_obj_set_style_bg_color(widgets->fill, provider_color, 0);
}

static void apply_provider(provider_page *page, const tk_tokens *tokens,
                           usage_provider provider) {
  usage_hero_view view = {0};
  usage_presenter_build_hero(tokens, provider, &view);
  apply_hero(&page->hero, &view,
             provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX);
}

static void apply_forecast_row(usage_card_widgets *widgets,
                               const usage_forecast_row_view *row) {
  lv_color_t provider_color = row->provider == USAGE_PROVIDER_CLAUDE
                                  ? COL_CLAUDE : COL_CODEX;
  lv_label_set_text(widgets->label, row->label);
  lv_label_set_text(widgets->delta, "");
  lv_label_set_text(widgets->pct, row->pct_text);
  lv_label_set_text(widgets->reset_short, row->headline);
  lv_label_set_text(widgets->reset, row->detail);
  lv_obj_add_flag(widgets->pct_suffix, LV_OBJ_FLAG_HIDDEN);

  double pct = row->has_pct ? row->pct : 0.0;
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  int fill = (int)llround(pct * widgets->track_w / 100.0);
  lv_obj_set_width(widgets->before_fill, fill);
  lv_obj_set_width(widgets->today_fill, 0);
  lv_obj_set_style_bg_color(widgets->before_fill, provider_color, 0);
}

static void apply_forecasts(const tk_tokens *tokens) {
  usage_forecast_page_view view;
  usage_presenter_build_forecasts(tokens, &view);
  for (int i = 0; i < 2; i++) {
    apply_forecast_row(&ui.forecast_cards[i], &view.rows[i]);
  }
}

static void set_summary_label(lv_obj_t *label, const char *source) {
  lv_label_set_text(label, source ? source : "");
}

static void apply_summary_row(summary_row_widgets *widgets,
                              usage_provider provider,
                              const usage_card_view *card) {
  lv_color_t provider_color = provider == USAGE_PROVIDER_CLAUDE
                                  ? COL_CLAUDE : COL_CODEX;
  set_summary_label(widgets->label, card->label);
  lv_label_set_text(widgets->pct, card->has_pct ? card->pct_text : "–");
  lv_label_set_text(widgets->used,
                    card->has_delta ? card->delta_text : "");
  lv_label_set_text(widgets->reset, card->reset_text);

  double pct = card->has_pct ? card->pct : 0.0;
  if (pct < 0.0) pct = 0.0;
  if (pct > 100.0) pct = 100.0;
  int fill_w = (int)llround(pct * CONTENT_W / 100.0);
  lv_obj_set_width(widgets->fill, fill_w);
  lv_obj_set_style_bg_color(widgets->fill, provider_color, 0);
  lv_obj_set_style_bg_color(widgets->status_dot,
                            card->has_pct ? provider_color : COL_MUTED, 0);
}

static void apply_claude_details(const tk_tokens *tokens) {
  usage_detail_page_view view = {0};
  usage_presenter_build_claude_details(tokens, &view);
  for (int i = 0; i < view.row_count; i++) {
    apply_summary_row(&ui.claude_details.rows[i], USAGE_PROVIDER_CLAUDE,
                      &view.rows[i]);
  }
}

static void apply_overview(const tk_tokens *tokens) {
  usage_overview_page_view view = {0};
  usage_presenter_build_overview(tokens, &view);
  for (int i = 0; i < view.row_count; i++) {
    apply_summary_row(&ui.overview.rows[i], view.rows[i].provider,
                      &view.rows[i].quota);
  }
}

static void apply_metadata(provider_page *page,
                           const tk_agent_status *agent) {
  lv_label_set_text(page->hero.model,
                    agent && agent->has_model ? agent->model : "");
}

static void create_hero_page(lv_obj_t *tile, hero_widgets *hero,
                             const char *provider) {
  hero->tile = tile;
  hero->content = bare(tile);
  lv_obj_set_pos(hero->content, SAFE_X, 0);
  lv_obj_set_size(hero->content, CONTENT_W, SCREEN_W);

  create_app_icon(hero->content, 0, HEADER_Y + 7);
  hero->provider = text(hero->content, &plex_text_21, COL_WHITE);
  lv_obj_set_pos(hero->provider, 41, HEADER_Y + 15);
  lv_obj_set_width(hero->provider, 180);
  lv_obj_set_style_text_letter_space(hero->provider, 2, 0);
  lv_label_set_text(hero->provider, provider);

  hero->model = text(hero->content, &plex_ui_14, lv_color_hex(0xE9EBEF));
  lv_obj_set_pos(hero->model, 210, HEADER_Y + 15);
  lv_obj_set_size(hero->model, CONTENT_W - 210, 22);
  lv_obj_set_style_text_align(hero->model, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(hero->model, 1, 0);
  lv_label_set_text(hero->model, "");

  lv_obj_t *line = bare(hero->content);
  lv_obj_set_pos(line, 0, HEADER_Y + HEADER_H - 1);
  lv_obj_set_size(line, CONTENT_W, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_HAIRLINE, 0);

  hero->quota = text(hero->content, &plex_text_21, COL_LABEL);
  lv_obj_set_pos(hero->quota, 0, HERO_LABEL_Y);
  lv_obj_set_size(hero->quota, CONTENT_W, 28);
  lv_obj_set_style_text_letter_space(hero->quota, 2, 0);
  lv_label_set_text(hero->quota, "");

  hero->pct = text(hero->content, &plex_num_146, COL_WHITE);
  lv_obj_set_pos(hero->pct, 0, HERO_PCT_Y);
  lv_obj_set_size(hero->pct, CONTENT_W, HERO_PCT_H);
  lv_label_set_long_mode(hero->pct, LV_LABEL_LONG_CLIP);
  lv_label_set_text(hero->pct, "–");

  hero->track = bare(hero->content);
  lv_obj_set_pos(hero->track, 0, HERO_BAR_Y);
  lv_obj_set_size(hero->track, CONTENT_W, HERO_BAR_H);
  lv_obj_set_style_bg_opa(hero->track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(hero->track, COL_TRACK, 0);
  lv_obj_set_style_clip_corner(hero->track, true, 0);

  hero->fill = bare(hero->track);
  lv_obj_set_pos(hero->fill, 0, 0);
  lv_obj_set_size(hero->fill, 0, HERO_BAR_H);
  lv_obj_set_style_bg_opa(hero->fill, LV_OPA_COVER, 0);

  hero->reset = text(hero->content, &plex_text_21, COL_RESET);
  lv_obj_set_pos(hero->reset, 0, HERO_RESET_Y);
  lv_obj_set_size(hero->reset, CONTENT_W, 28);
  lv_obj_set_style_text_letter_space(hero->reset, 1, 0);
  lv_label_set_text(hero->reset, "");
}

static void create_summary_header(summary_page *page, const char *title) {
  page->content = bare(page->tile);
  lv_obj_set_pos(page->content, SAFE_X, 0);
  lv_obj_set_size(page->content, CONTENT_W, SCREEN_W);

  create_app_icon(page->content, 0, HEADER_Y + 7);
  lv_obj_t *label = text(page->content, &plex_text_21, COL_WHITE);
  lv_obj_set_pos(label, 41, HEADER_Y + 15);
  lv_obj_set_width(label, CONTENT_W - 41);
  lv_obj_set_style_text_letter_space(label, 2, 0);
  lv_label_set_text(label, title);

  lv_obj_t *line = bare(page->content);
  lv_obj_set_pos(line, 0, HEADER_Y + HEADER_H - 1);
  lv_obj_set_size(line, CONTENT_W, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_HAIRLINE, 0);
}

static void create_summary_row(lv_obj_t *content, summary_row_widgets *row,
                               int y, usage_provider provider) {
  if (provider == USAGE_PROVIDER_CLAUDE) {
    row->provider_icon = lv_image_create(content);
    lv_image_set_src(row->provider_icon, &tk_img_claude_32);
    lv_obj_set_pos(row->provider_icon, 0, y);
    lv_obj_set_style_image_recolor(row->provider_icon, COL_CLAUDE, 0);
    lv_obj_set_style_image_recolor_opa(row->provider_icon, LV_OPA_COVER, 0);
  } else {
    row->provider_icon = create_codex_icon_32(content, 0, y);
  }

  row->provider = text(content, &plex_text_21, COL_WHITE);
  lv_obj_set_pos(row->provider, 42, y + 5);
  lv_obj_set_style_text_letter_space(row->provider, 2, 0);
  lv_label_set_text(row->provider,
                    provider == USAGE_PROVIDER_CLAUDE ? "CLAUDE" : "CODEX");

  row->label = text(content, &plex_ui_14, COL_LABEL);
  lv_obj_set_pos(row->label, 0, y + 43);
  lv_obj_set_size(row->label, SUMMARY_PCT_X, 18);
  lv_obj_set_style_text_letter_space(row->label, 1, 0);
  lv_obj_set_style_transform_scale_x(row->label, 256, 0);
  lv_obj_set_style_transform_scale_y(row->label, SUMMARY_LABEL_SCALE_Y, 0);
  lv_obj_set_style_transform_pivot_x(row->label, 0, 0);
  lv_obj_set_style_transform_pivot_y(row->label, 0, 0);
  lv_label_set_long_mode(row->label, LV_LABEL_LONG_CLIP);
  lv_label_set_text(row->label, "");

  row->pct = text(content, &plex_num_118, COL_WHITE);
  lv_obj_set_pos(row->pct, SUMMARY_PCT_X, y);
  lv_obj_set_size(row->pct, SUMMARY_PCT_W, 130);
  lv_obj_set_style_text_align(row->pct, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(row->pct, -8, 0);
  lv_label_set_long_mode(row->pct, LV_LABEL_LONG_CLIP);
  lv_label_set_text(row->pct, "–");

  row->used = text(content, &plex_ui_14,
                   provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX);
  lv_obj_set_pos(row->used, 0, y + 84);
  lv_obj_set_width(row->used, SUMMARY_PCT_X);
  lv_obj_set_style_text_letter_space(row->used, 1, 0);
  lv_label_set_text(row->used, "");

  row->track = bare(content);
  lv_obj_set_pos(row->track, 0, y + SUMMARY_TRACK_Y);
  lv_obj_set_size(row->track, CONTENT_W, 16);
  lv_obj_set_style_radius(row->track, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(row->track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(row->track, COL_TRACK, 0);
  lv_obj_set_style_clip_corner(row->track, true, 0);

  row->fill = bare(row->track);
  lv_obj_set_pos(row->fill, 0, 0);
  lv_obj_set_size(row->fill, 0, 16);
  lv_obj_set_style_bg_opa(row->fill, LV_OPA_COVER, 0);

  row->reset = text(content, &plex_text_17, COL_RESET);
  lv_obj_set_pos(row->reset, 0, y + SUMMARY_RESET_Y);
  lv_obj_set_width(row->reset, CONTENT_W);
  lv_obj_set_style_text_letter_space(row->reset, 1, 0);
  lv_label_set_text(row->reset, "");

  row->status_dot = bare(content);
  lv_obj_set_pos(row->status_dot, 140, y + 88);
  lv_obj_set_size(row->status_dot, 8, 8);
  lv_obj_set_style_radius(row->status_dot, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(row->status_dot, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(row->status_dot, COL_MUTED, 0);
}

static void create_summary_hairline(summary_page *page) {
  lv_obj_t *line = bare(page->content);
  lv_obj_set_pos(line, 0, 258);
  lv_obj_set_size(line, CONTENT_W, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_HAIRLINE, 0);
}

static void create_summary_pager(summary_page *page, int active) {
  lv_obj_t *box = bare(page->content);
  lv_obj_set_pos(box, 354, 33);
  lv_obj_set_size(box, 76, 16);
  lv_obj_set_flex_flow(box, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(box, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(box, 5, 0);
  for (int i = 0; i < TK_USAGE_SCREEN_VIEWS; i++) {
    lv_obj_t *dot = bare(box);
    lv_obj_set_size(dot, i == active ? 13 : 5, 5);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(dot, i == active ? COL_DOT_ON : COL_DOT, 0);
  }
}

static void create_claude_details_page(void) {
  summary_page *page = &ui.claude_details;
  page->tile = new_tile(VIEW_CLAUDE_DETAILS);
  create_summary_header(page, "CLAUDE DETAILS");
  create_summary_row(page->content, &page->rows[0], SUMMARY_ROW_1_Y,
                     USAGE_PROVIDER_CLAUDE);
  create_summary_row(page->content, &page->rows[1], SUMMARY_ROW_2_Y,
                     USAGE_PROVIDER_CLAUDE);
  create_summary_hairline(page);
  create_summary_pager(page, VIEW_CLAUDE_DETAILS);
}

static void create_overview_page(void) {
  summary_page *page = &ui.overview;
  page->tile = new_tile(VIEW_OVERVIEW);
  create_summary_header(page, "OVERVIEW");
  create_summary_row(page->content, &page->rows[0], SUMMARY_ROW_1_Y,
                     USAGE_PROVIDER_CLAUDE);
  create_summary_row(page->content, &page->rows[1], SUMMARY_ROW_2_Y,
                     USAGE_PROVIDER_CODEX);
  create_summary_hairline(page);
  create_summary_pager(page, VIEW_OVERVIEW);
}

static void create_forecast_page(void) {
  lv_obj_t *tile = new_tile(VIEW_FORECAST);
  create_header(tile, "VECKOTAKT");
  create_card(tile, &ui.forecast_cards[0], CARD_1_Y, CARD_H, false);
  create_card(tile, &ui.forecast_cards[1], CARD_2_Y, CARD_H, false);
  create_pager(tile, VIEW_FORECAST);
}

static void create_volume_page(void) {
  lv_obj_t *tile = new_tile(VIEW_VOLUME);
  create_header(tile, "VOLYM");
  lv_obj_t *card = bare(tile);
  lv_obj_set_pos(card, LEGACY_CONTENT_X, 88);
  lv_obj_set_size(card, LEGACY_CONTENT_W, 245);
  lv_obj_set_style_bg_opa(card, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(card, COL_CARD, 0);
  lv_obj_set_style_border_width(card, 1, 0);
  lv_obj_set_style_border_color(card, COL_BORDER, 0);
  lv_obj_set_style_radius(card, 12, 0);

  lv_obj_t *caption = text(card, &plex_ui_14, COL_LABEL);
  lv_obj_set_pos(caption, 18, 18);
  lv_obj_set_style_text_letter_space(caption, 2, 0);
  lv_label_set_text(caption, "CLAUDE IDAG · MTOK");
  ui.volume_value = text(card, &plex_num_118, COL_WHITE);
  lv_obj_set_pos(ui.volume_value, 18, 52);
  lv_obj_set_size(ui.volume_value, 400, 130);
  lv_label_set_text(ui.volume_value, "–");
  ui.volume_sessions = text(card, &plex_text_17, COL_RESET);
  lv_obj_set_pos(ui.volume_sessions, 18, 190);
  ui.volume_month = text(card, &plex_text_17, COL_MUTED);
  lv_obj_set_pos(ui.volume_month, 220, 190);
  lv_obj_set_size(ui.volume_month, 200, 24);
  lv_obj_set_style_text_align(ui.volume_month, LV_TEXT_ALIGN_RIGHT, 0);
  create_pager(tile, VIEW_VOLUME);
}

void usage_screen_create(lv_obj_t *root) {
  memset(&ui, 0, sizeof ui);
  ui.tileview = lv_tileview_create(root);
  lv_obj_set_size(ui.tileview, SCREEN_W, SCREEN_W);
  lv_obj_set_scrollbar_mode(ui.tileview, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_opa(ui.tileview, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(ui.tileview, COL_BLACK, 0);

  ui.claude.tile = new_tile(VIEW_CLAUDE_HERO);
  create_hero_page(ui.claude.tile, &ui.claude.hero, "CLAUDE");
  create_pager(ui.claude.tile, VIEW_CLAUDE_HERO);

  ui.codex.tile = new_tile(VIEW_CODEX_HERO);
  create_hero_page(ui.codex.tile, &ui.codex.hero, "CODEX");
  create_pager(ui.codex.tile, VIEW_CODEX_HERO);

  create_claude_details_page();
  create_overview_page();
  create_forecast_page();
  create_volume_page();
  tk_agent_monitor_create(root);
}

void usage_screen_apply_tokens(const tk_tokens *tokens) {
  if (!tokens) return;
  apply_provider(&ui.claude, tokens, USAGE_PROVIDER_CLAUDE);
  apply_provider(&ui.codex, tokens, USAGE_PROVIDER_CODEX);
  apply_claude_details(tokens);
  apply_overview(tokens);
  apply_forecasts(tokens);
}

void usage_screen_apply_agent(const tk_agent_snapshot *snapshot,
                              int64_t now_us) {
  if (!snapshot) return;
  apply_metadata(&ui.claude, tk_agent_provider_primary(&snapshot->claude));
  apply_metadata(&ui.codex, tk_agent_provider_primary(&snapshot->codex));
  tk_agent_monitor_apply(snapshot, now_us);
}

void usage_screen_tick(int64_t now_us) {
  tk_agent_monitor_tick(now_us);
}

void usage_screen_set_volume(double day_mtok, int sessions,
                             double month_mtok) {
  char buffer[48];
  if (isfinite(day_mtok)) {
    snprintf(buffer, sizeof buffer, "%.1f", day_mtok);
    lv_label_set_text(ui.volume_value, buffer);
  }
  snprintf(buffer, sizeof buffer, "%d SESSIONER", sessions);
  lv_label_set_text(ui.volume_sessions, buffer);
  snprintf(buffer, sizeof buffer, "%.0f MTOK MÅNAD", month_mtok);
  lv_label_set_text(ui.volume_month, buffer);
}

void usage_screen_set_stale(bool stale) {
  ui.stale = stale;
  /* A stale transport state only changes the header treatment. The rendered
   * quota is the last accepted presenter value until fresh tokens arrive. */
  lv_color_t color = stale ? COL_STALE : COL_WHITE;
  lv_obj_set_style_text_color(ui.claude.hero.provider, color, 0);
  lv_obj_set_style_text_color(ui.codex.hero.provider, color, 0);
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
  return VIEW_CLAUDE_HERO;
}
