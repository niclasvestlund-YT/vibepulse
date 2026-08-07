#include "usage_screen.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "agent_monitor.h"
#include "usage_presenter.h"
#include "torget.h"

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
#define COL_CLAUDE      lv_color_hex(0xFF7C61)
#define COL_CLAUDE_OLD  lv_color_hex(0x70463E)
#define COL_CODEX       lv_color_hex(0x6F78FF)
#define COL_CODEX_OLD   lv_color_hex(0x3E436F)
#define COL_APP_PLATE   lv_color_hex(0x181636)
#define COL_APP_DOT     lv_color_hex(0x7770FF)
#define COL_DOT         lv_color_hex(0x41444A)
#define COL_DOT_ON      lv_color_hex(0xCDD2DA)
#define COL_STALE       lv_color_hex(0x5C687B)

#define SCREEN_W 480
#define CONTENT_X 18
#define CONTENT_W 444
#define HEADER_Y 15
#define HEADER_H 52
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
  lv_obj_t *provider;
  lv_obj_t *model;
  lv_obj_t *effort;
  usage_card_widgets cards[2];
  int provider_index;
} provider_page;

static struct {
  lv_obj_t *tileview;
  lv_obj_t *tiles[TK_USAGE_SCREEN_VIEWS];
  lv_obj_t *dots[TK_USAGE_SCREEN_VIEWS];
  provider_page claude;
  provider_page codex;
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

static void create_app_icon(lv_obj_t *parent) {
  lv_obj_t *plate = bare(parent);
  lv_obj_set_pos(plate, CONTENT_X, HEADER_Y + 9);
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

static void create_header(lv_obj_t *tile, provider_page *page,
                          const char *provider) {
  create_app_icon(tile);
  page->provider = text(tile, &plex_text_21, COL_WHITE);
  lv_obj_set_pos(page->provider, 63, HEADER_Y + 15);
  lv_obj_set_width(page->provider, 190);
  lv_obj_set_style_text_letter_space(page->provider, 2, 0);
  lv_label_set_text(page->provider, provider);

  page->model = text(tile, &plex_ui_14, lv_color_hex(0xE9EBEF));
  lv_obj_set_pos(page->model, 270, HEADER_Y + 7);
  lv_obj_set_size(page->model, 192, 22);
  lv_obj_set_style_text_align(page->model, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(page->model, 1, 0);
  lv_label_set_text(page->model, "");

  page->effort = text(tile, &plex_ui_12, COL_MUTED);
  lv_obj_set_pos(page->effort, 270, HEADER_Y + 30);
  lv_obj_set_size(page->effort, 192, 18);
  lv_obj_set_style_text_align(page->effort, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(page->effort, 2, 0);
  lv_label_set_text(page->effort, "");

  lv_obj_t *line = bare(tile);
  lv_obj_set_pos(line, CONTENT_X, HEADER_Y + HEADER_H - 1);
  lv_obj_set_size(line, CONTENT_W, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_HAIRLINE, 0);
}

static void create_card(lv_obj_t *tile, usage_card_widgets *card, int y,
                        int height, bool large) {
  card->root = bare(tile);
  lv_obj_set_pos(card->root, CONTENT_X, y);
  lv_obj_set_size(card->root, CONTENT_W, height);
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

  card->track_w = CONTENT_W - 28;
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

static void apply_card(usage_card_widgets *widgets,
                       const usage_card_view *card,
                       lv_color_t old_color, lv_color_t today_color) {
  lv_label_set_text(widgets->label, card->label);
  lv_label_set_text(widgets->delta, card->delta_text);
  lv_label_set_text(widgets->reset_short, card->reset_short_text);
  lv_label_set_text(widgets->reset, card->reset_text);

  if (card->has_pct) {
    lv_label_set_text(widgets->pct, card->pct_text);
  } else {
    lv_label_set_text(widgets->pct, "–");
  }

  double pct = card->has_pct ? card->pct : 0.0;
  double delta = card->has_delta ? card->delta_pct : 0.0;
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  if (delta < 0) delta = 0;
  if (delta > pct) delta = pct;
  int before = (int)llround((pct - delta) * widgets->track_w / 100.0);
  int today = (int)llround(delta * widgets->track_w / 100.0);
  lv_obj_set_width(widgets->before_fill, before);
  lv_obj_set_width(widgets->today_fill, today);
  lv_obj_set_x(widgets->today_fill, before);
  lv_obj_set_style_bg_color(widgets->before_fill, old_color, 0);
  lv_obj_set_style_bg_color(widgets->today_fill, today_color, 0);
  lv_obj_set_style_text_color(widgets->delta, today_color, 0);
}

static void apply_provider(provider_page *page, const tk_tokens *tokens,
                           usage_provider provider) {
  usage_provider_view view;
  /* Den statiska AMOLED-grinden visar alltid det första läget. Rotation
   * kopplas på först efter fysisk läsbarhetskontroll. */
  usage_presenter_build_provider(tokens, provider, 0, &view);
  lv_color_t old_color = provider == USAGE_PROVIDER_CLAUDE
                             ? COL_CLAUDE_OLD : COL_CODEX_OLD;
  lv_color_t today_color = provider == USAGE_PROVIDER_CLAUDE
                               ? COL_CLAUDE : COL_CODEX;
  for (int i = 0; i < 2; i++) {
    if (!page->cards[i].root) continue;
    if (i < view.card_count) {
      lv_obj_remove_flag(page->cards[i].root, LV_OBJ_FLAG_HIDDEN);
      apply_card(&page->cards[i], &view.cards[i], old_color, today_color);
    } else {
      lv_obj_add_flag(page->cards[i].root, LV_OBJ_FLAG_HIDDEN);
    }
  }
}

static void apply_metadata(provider_page *page,
                           const tk_agent_status *agent) {
  lv_label_set_text(page->model,
                    agent && agent->has_model ? agent->model : "");
  lv_label_set_text(page->effort,
                    agent && agent->has_effort ? agent->effort : "");
}

static void create_forecast_shell(void) {
  lv_obj_t *tile = new_tile(2);
  provider_page page = {0};
  create_header(tile, &page, "VECKOTAKT");
  usage_card_widgets claude = {0}, codex = {0};
  create_card(tile, &claude, CARD_1_Y, CARD_H, false);
  create_card(tile, &codex, CARD_2_Y, CARD_H, false);
  lv_label_set_text(claude.label, "CLAUDE · VECKA");
  lv_label_set_text(codex.label, "CODEX · VECKA");
  lv_label_set_text(claude.pct, "–");
  lv_label_set_text(codex.pct, "–");
  lv_obj_add_flag(claude.pct_suffix, LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(codex.pct_suffix, LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text(claude.reset_short, "SAMLAR TAKT");
  lv_label_set_text(codex.reset_short, "SAMLAR TAKT");
  lv_label_set_text(claude.reset, "PROGNOS EFTER AMOLED-TITT");
  lv_label_set_text(codex.reset, "PROGNOS EFTER AMOLED-TITT");
  create_pager(tile, 2);
}

static void create_volume_page(void) {
  lv_obj_t *tile = new_tile(3);
  provider_page page = {0};
  create_header(tile, &page, "VOLYM");
  lv_obj_t *card = bare(tile);
  lv_obj_set_pos(card, CONTENT_X, 88);
  lv_obj_set_size(card, CONTENT_W, 245);
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
  create_pager(tile, 3);
}

void usage_screen_create(lv_obj_t *root) {
  memset(&ui, 0, sizeof ui);
  ui.tileview = lv_tileview_create(root);
  lv_obj_set_size(ui.tileview, SCREEN_W, SCREEN_W);
  lv_obj_set_scrollbar_mode(ui.tileview, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_opa(ui.tileview, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(ui.tileview, COL_BLACK, 0);

  ui.claude.provider_index = 0;
  ui.claude.tile = new_tile(0);
  create_header(ui.claude.tile, &ui.claude, "CLAUDE");
  create_card(ui.claude.tile, &ui.claude.cards[0], CARD_1_Y, CARD_H, false);
  create_card(ui.claude.tile, &ui.claude.cards[1], CARD_2_Y, CARD_H, false);
  create_pager(ui.claude.tile, 0);
  lv_obj_t *claude_footer = bare(ui.claude.tile);
  lv_obj_set_pos(claude_footer, CONTENT_X, FOOTER_Y);
  lv_obj_set_size(claude_footer, CONTENT_W, FOOTER_H);
  tk_agent_monitor_create_footer(claude_footer, true);

  ui.codex.provider_index = 1;
  ui.codex.tile = new_tile(1);
  create_header(ui.codex.tile, &ui.codex, "CODEX");
  create_card(ui.codex.tile, &ui.codex.cards[0], 90, 230, true);
  create_pager(ui.codex.tile, 1);
  lv_obj_t *codex_footer = bare(ui.codex.tile);
  lv_obj_set_pos(codex_footer, CONTENT_X, FOOTER_Y);
  lv_obj_set_size(codex_footer, CONTENT_W, FOOTER_H);
  tk_agent_monitor_create_footer(codex_footer, false);

  create_forecast_shell();
  create_volume_page();
}

void usage_screen_apply_tokens(const tk_tokens *tokens) {
  if (!tokens) return;
  apply_provider(&ui.claude, tokens, USAGE_PROVIDER_CLAUDE);
  apply_provider(&ui.codex, tokens, USAGE_PROVIDER_CODEX);
}

void usage_screen_apply_agent(const tk_agent_snapshot *snapshot,
                              int64_t now_us) {
  if (!snapshot) return;
  apply_metadata(&ui.claude, &snapshot->claude);
  apply_metadata(&ui.codex, &snapshot->codex);
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
  lv_color_t color = stale ? COL_STALE : COL_WHITE;
  lv_obj_set_style_text_color(ui.claude.provider, color, 0);
  lv_obj_set_style_text_color(ui.codex.provider, color, 0);
}

void usage_screen_show_view(int index) {
  if (index < 0) index = 0;
  if (index >= TK_USAGE_SCREEN_VIEWS) index = TK_USAGE_SCREEN_VIEWS - 1;
  lv_obj_set_tile_id(ui.tileview, index, 0, LV_ANIM_OFF);
}
