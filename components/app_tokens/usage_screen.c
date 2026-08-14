#include "usage_screen.h"

#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_monitor.h"
#include "app_tokens.h"
#include "max_tracker.h"
#include "max_tracker_presenter.h"
#include "torget.h"
#include "usage_live_policy.h"
#include "usage_presenter.h"
#include "vibepulse_layout.generated.h"

extern const lv_font_t plex_num_164;
extern const lv_font_t plex_money_118;
extern const lv_font_t plex_money_35;
extern const lv_font_t plex_num_38;
extern const lv_font_t plex_headline_48;
extern const lv_font_t plex_stat_35;
extern const lv_font_t plex_ui_21;
extern const lv_font_t plex_ui_16;
extern const lv_font_t plex_ui_14;
extern const lv_font_t plex_ui_12;
extern const lv_font_t plex_text_21;
extern const lv_font_t plex_text_16;
extern const lv_font_t plex_text_17;

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

/* Max Tracker geometry — approved 2026-08-12 mocks, matches the studio
 * design tokens: content safe X 22/width 436, grid indented a further
 * 9-10 px each side so the heatmap reads as its own object. */
#define MT_EYEBROW_Y (VP_QUOTA_Y + 4)
#define MT_GRID_X 31
#define MT_GRID_Y 112
#define MT_CELL 18
#define MT_GAP 3
#define MT_PITCH (MT_CELL + MT_GAP)
#define MT_ROWS 7
#define MT_GRID_W (TK_MT_WEEKS * MT_PITCH - MT_GAP)  /* 417 */
#define MT_GRID_H (MT_ROWS * MT_PITCH - MT_GAP)      /* 144 */
#define MT_GRID_RIGHT (MT_GRID_X + MT_GRID_W)         /* 448 */
#define MT_LEGEND_SWATCH 12
#define MT_LEGEND_GAP 3
#define MT_LEGEND_BLOCK_W (5 * MT_LEGEND_SWATCH + 4 * MT_LEGEND_GAP) /* 72 */
#define MT_LEGEND_LABEL_W 40
#define MT_LEGEND_LABEL_GAP 8
#define MT_LEGEND_Y (MT_GRID_Y + MT_GRID_H + 10)      /* 266 */
#define MT_DRAW_H (MT_LEGEND_Y - MT_GRID_Y + MT_LEGEND_SWATCH) /* 166 */
#define MT_STAT_LINE_Y (MT_LEGEND_Y + MT_LEGEND_SWATCH + 20)   /* 298 */
#define MT_STAT_LABEL_Y (MT_STAT_LINE_Y + 16)
#define MT_STAT_VALUE_Y (MT_STAT_LABEL_Y + 34)
#define MT_STAT_COL_W (MT_GRID_W / 4)

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
  char rendered_context[64];
  bool context_initialized;
  bool halo_visible;
  bool halo_initialized;
  bool has_data;
  bool quota_stale;
} quota_page;

typedef struct {
  lv_obj_t *root;
  lv_obj_t *label;
  lv_obj_t *headline;
  lv_obj_t *detail;
} forecast_row;

typedef struct {
  lv_obj_t *tile;
  lv_obj_t *halo;
  lv_obj_t *context;
  lv_obj_t *grid;
  lv_obj_t *plan_badge;
  lv_obj_t *stat_value[4];
  lv_obj_t *stat_unit[4];
  usage_provider provider;
  bool codex;
  bool has_data;
  int coding_streak_days;
  tk_mt_provider data;
  char rendered_context[64];
  bool context_initialized;
  bool halo_visible;
  bool halo_initialized;
  bool quota_stale;
} tracker_page;

typedef struct {
  lv_obj_t *tile;
  lv_obj_t *verdict;
  lv_obj_t *hero;
  lv_obj_t *attribution;
  lv_obj_t *track;
  lv_obj_t *fill[2];      /* one segment per counted provider */
  lv_obj_t *marker;
  lv_obj_t *stat_api, *stat_paid;
  lv_obj_t *cap_api, *cap_break, *cap_paid;
} value_page;

static struct {
  lv_obj_t *tileview;
  lv_obj_t *tiles[TK_USAGE_SCREEN_VIEWS];
  quota_page quotas[3];
  forecast_row forecast_rows[2];
  tracker_page trackers[2];
  value_page value;
  tk_tokens last_tokens;
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

/* Självstorlekande etikett — trackerns statvärde/enhet-par behöver sin
 * riktiga renderade bredd (LV_SIZE_CONTENT) för att kunna placera enheten
 * direkt efter talet, till skillnad från de fastbredda etiketterna ovan. */
static lv_obj_t *label_auto(lv_obj_t *parent, const lv_font_t *font,
                            lv_color_t color, int x, int y) {
  lv_obj_t *object = lv_label_create(parent);
  lv_obj_set_style_text_font(object, font, 0);
  lv_obj_set_style_text_color(object, color, 0);
  lv_obj_set_pos(object, x, y);
  lv_obj_set_size(object, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static void open_launcher(lv_event_t *event) {
  (void)event;
  torget_launcher_open();
}

static lv_obj_t *create_codex_icon(lv_obj_t *parent, int x, int y) {
  lv_obj_t *image = lv_image_create(parent);
  lv_image_set_src(image, &tk_img_codex_32);
  lv_obj_remove_flag(image, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_pos(image, x, y);
  return image;
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

/* Delad huvudkonstruktion: providerikon + namn, halo (dold tills en aktiv
 * chatt syns), höger-justerad livsstatus, hårlinje. Kvot- och
 * trackersidorna bygger EXAKT samma widgetar härifrån — bara vilka fält de
 * lagrar dem i skiljer sig. */
static void create_live_header_widgets(lv_obj_t *tile, usage_provider provider,
                                       lv_obj_t **halo_out,
                                       lv_obj_t **context_out) {
  lv_obj_t *halo = bare(tile);
  lv_obj_set_pos(halo, 18, 14);
  lv_obj_set_size(halo, 40, 40);
  lv_obj_set_style_radius(halo, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_border_width(halo, 2, 0);
  lv_obj_set_style_border_opa(halo, LV_OPA_COVER, 0);
  lv_obj_set_style_border_color(
      halo, provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX, 0);
  lv_obj_add_flag(halo, LV_OBJ_FLAG_HIDDEN);

  create_provider_identity(tile, provider);
  lv_obj_t *context = label(tile, &plex_ui_14, COL_META,
                            180, VP_PROVIDER_Y + 5, 278, 20);
  lv_obj_set_style_text_align(context, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(context, 1, 0);
  create_hairline(tile, HEADER_LINE_Y);

  *halo_out = halo;
  *context_out = context;
}

static void create_quota_header(quota_page *page) {
  create_live_header_widgets(page->tile, page->provider, &page->halo,
                             &page->context);
  page->halo_initialized = true;
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

/* Centred from the dot geometry rather than a hard-coded origin: the row
 * grows every time a view is added, and a fixed origin walks it off centre. */
#define PAGER_DOT 6
#define PAGER_DOT_ACTIVE 18
#define PAGER_GAP 5
#define PAGER_W ((TK_USAGE_SCREEN_VIEWS - 1) * (PAGER_DOT + PAGER_GAP) + \
                 PAGER_DOT_ACTIVE)
#define PAGER_X ((VP_SCREEN_W - PAGER_W) / 2)

static void create_pager(lv_obj_t *tile, int active) {
  int x = PAGER_X;
  for (int i = 0; i < TK_USAGE_SCREEN_VIEWS; i++) {
    int width = i == active ? PAGER_DOT_ACTIVE : PAGER_DOT;
    lv_obj_t *dot = bare(tile);
    lv_obj_set_pos(dot, x, PAGER_Y);
    lv_obj_set_size(dot, width, PAGER_DOT);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(dot, i == active ? COL_DOT_ON : COL_DOT, 0);
    x += width + PAGER_GAP;
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

/* Value page.
 *
 * One question: did the month cost less on a subscription than it would have
 * on the API? The verdict says it in words, the hero gives the ratio, and the
 * bar puts it against a break-even marker at the halfway point.
 *
 * It borrows the quota pages' own furniture deliberately -- bar on y=304 at
 * 24 px with the shared pill radius, the same 3x32 marker, stats on the
 * family's rows -- because that is what makes it read as this product rather
 * than as a page from somewhere else. Only the hero font differs, and only
 * because the 164 px numerals carry no "$" or "x" and adding either would
 * shift all four approved quota rasters. */
#define VALUE_HERO_X 18
#define VALUE_HERO_Y 143   /* ink top lands on 151, the quota hero's own */
#define VALUE_MONEY_HERO_Y 151
#define VALUE_WORD_HERO_Y 150
#define VALUE_VERDICT_Y 72
#define VALUE_ATTRIB_Y 272
/* Break-even is half scale, and half the content width is the screen centre. */
#define VALUE_MARKER_X (VP_SAFE_X + VP_CONTENT_W / 2 - 1)
/* plex_money_35's line_height is 3 px taller than the quota stat font's, so
 * y=349 puts its digit ink on the family's 352 row. Do not "fix" to 352. */
#define VALUE_STAT_Y 349

static void create_value_page(void) {
  value_page *page = &ui.value;
  memset(page, 0, sizeof *page);
  page->tile = new_tile(VIEW_VALUE);
  create_analytics_header(page->tile, "VALUE", "MONTH TO DATE",
                          "AT LIST API PRICES");

  page->verdict = label(page->tile, &plex_ui_21, COL_LABEL,
                        VP_SAFE_X, VALUE_VERDICT_Y, VP_CONTENT_W, 26);
  lv_obj_set_style_text_letter_space(page->verdict, 2, 0);

  page->hero = label_auto(page->tile, &plex_money_118, COL_WHITE,
                          VALUE_HERO_X, VALUE_HERO_Y);
  lv_obj_set_style_text_letter_space(page->hero, -3, 0);
  lv_label_set_text(page->hero, "–");

  page->attribution = label(page->tile, &plex_ui_16, COL_MUTED,
                            VP_SAFE_X, VALUE_ATTRIB_Y, VP_CONTENT_W, 22);
  lv_obj_set_style_text_letter_space(page->attribution, 2, 0);

  page->track = bare(page->tile);
  lv_obj_set_pos(page->track, VP_SAFE_X, VP_BAR_Y);
  lv_obj_set_size(page->track, VP_CONTENT_W, VP_BAR_H);
  lv_obj_set_style_bg_opa(page->track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(page->track, COL_TRACK, 0);
  lv_obj_set_style_radius(page->track, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_clip_corner(page->track, true, 0);

  for (int i = 0; i < 2; i++) {
    page->fill[i] = bare(page->track);
    lv_obj_set_pos(page->fill[i], 0, 0);
    lv_obj_set_size(page->fill[i], 0, VP_BAR_H);
    lv_obj_set_style_bg_opa(page->fill[i], LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(page->fill[i], COL_CLAUDE, 0);
  }

  /* The family's own break-even mark, 3x32 proud of the bar -- not the 124 px
   * one this page used to invent. */
  page->marker = bare(page->tile);
  lv_obj_set_pos(page->marker, VALUE_MARKER_X, VP_BAR_Y - 4);
  lv_obj_set_size(page->marker, 3, VP_BAR_H + 8);
  lv_obj_set_style_bg_opa(page->marker, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(page->marker, COL_WHITE, 0);

  page->stat_api = label(page->tile, &plex_money_35, COL_WHITE,
                         VP_SAFE_X, VALUE_STAT_Y, 210, 38);
  page->stat_paid = label(page->tile, &plex_money_35, COL_WHITE,
                          240, VALUE_STAT_Y, 218, 38);
  lv_obj_set_style_text_align(page->stat_paid, LV_TEXT_ALIGN_RIGHT, 0);

  page->cap_api = label(page->tile, &plex_ui_14, COL_MUTED,
                        VP_SAFE_X, STAT_LABEL_Y, 140, 20);
  page->cap_break = label(page->tile, &plex_ui_14, COL_MUTED,
                          170, STAT_LABEL_Y, 140, 20);
  page->cap_paid = label(page->tile, &plex_ui_14, COL_MUTED,
                         318, STAT_LABEL_Y, 140, 20);
  lv_obj_set_style_text_align(page->cap_break, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_align(page->cap_paid, LV_TEXT_ALIGN_RIGHT, 0);
  for (lv_obj_t **c = (lv_obj_t *[]){page->cap_api, page->cap_break,
                                     page->cap_paid, NULL}; *c; c++)
    lv_obj_set_style_text_letter_space(*c, 2, 0);
  lv_label_set_text(page->cap_api, "VIA API");
  lv_label_set_text(page->cap_break, "BREAK EVEN");
  lv_label_set_text(page->cap_paid, "YOU PAID");
  create_pager(page->tile, VIEW_VALUE);
}

/* The value page owns its hero font. The shared numeral fonts must never
 * carry "$": the glyph is taller than every digit, so adding it grows
 * line_height (164 px: 119 -> 153) and pushes every approved quota page down
 * 12 px. */
static void apply_value_hero(value_page *page,
                             const usage_value_page_view *view) {
  const bool word = view->hero_is_word;
  const bool money = view->state == USAGE_VALUE_NO_PLAN_COST;
  lv_obj_set_style_text_font(page->hero,
                             word ? &plex_headline_48 : &plex_money_118, 0);
  lv_obj_set_style_text_letter_space(page->hero, word ? -1 : -3, 0);
  lv_obj_set_pos(page->hero, word ? VP_SAFE_X : VALUE_HERO_X,
                 word ? VALUE_WORD_HERO_Y
                      : money ? VALUE_MONEY_HERO_Y : VALUE_HERO_Y);
  lv_label_set_text(page->hero, view->hero_text);
}

static void apply_value(const tk_tokens *tokens) {
  value_page *page = &ui.value;
  if (!page->tile) return;
  usage_value_page_view view;
  usage_presenter_build_value(tokens, &view);

  lv_label_set_text(page->verdict, view.verdict);
  lv_label_set_text(page->attribution, view.attribution);
  apply_value_hero(page, &view);

  int width = (int)(view.bar_fraction * VP_CONTENT_W + 0.5);
  if (width > VP_CONTENT_W) width = VP_CONTENT_W;
  if (view.show_bar && width < 6) width = 6;

  /* Segments in provider order, each sized by its share of the counted
   * value. Colour here is legitimate: a segment IS that provider's money. */
  int x = 0;
  for (int i = 0; i < 2; i++) {
    const bool live = view.show_bar && i < view.row_count &&
                      view.rows[i].counted && view.rows[i].share > 0;
    if (!live) {
      lv_obj_set_size(page->fill[i], 0, VP_BAR_H);
      continue;
    }
    int seg = (int)(view.rows[i].share * width + 0.5);
    if (x + seg > width) seg = width - x;
    lv_obj_set_style_bg_color(
        page->fill[i],
        view.rows[i].provider == USAGE_PROVIDER_CLAUDE ? COL_CLAUDE
                                                       : COL_CODEX, 0);
    lv_obj_set_pos(page->fill[i], x, 0);
    lv_obj_set_size(page->fill[i], seg, VP_BAR_H);
    x += seg;
  }

  lv_obj_t *const bar_parts[] = {page->track, page->marker, page->cap_break};
  for (size_t i = 0; i < sizeof bar_parts / sizeof bar_parts[0]; i++) {
    if (view.show_bar) lv_obj_remove_flag(bar_parts[i], LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(bar_parts[i], LV_OBJ_FLAG_HIDDEN);
  }
  if (view.show_bar) lv_obj_move_foreground(page->marker);

  const bool stats = view.api_cost[0] && view.paid[0];
  lv_obj_t *const footer[] = {page->stat_api, page->stat_paid,
                              page->cap_api, page->cap_paid};
  for (size_t i = 0; i < sizeof footer / sizeof footer[0]; i++) {
    if (stats) lv_obj_remove_flag(footer[i], LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(footer[i], LV_OBJ_FLAG_HIDDEN);
  }
  if (stats) {
    lv_label_set_text(page->stat_api, view.api_cost);
    lv_label_set_text(page->stat_paid, view.paid);
  }
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

/* Delad uppdateringskärna: samma liveheader-logik (halo + statustext) för
 * kvot- OCH trackersidorna, bara bokföringsfälten skiljer per sida. */
static void refresh_live_header(lv_obj_t *halo, lv_obj_t *context,
                                bool *halo_initialized, bool *halo_visible,
                                bool *context_initialized,
                                char *rendered_context,
                                size_t rendered_context_cap,
                                usage_provider provider, bool has_data,
                                bool stale, int64_t now_us) {
  usage_live_header_view view = {0};
  usage_live_build_header(agent_provider_for(provider),
                          agent_packet_age_ms(now_us), stale,
                          ui.has_agent_snapshot && has_data, &view);
  const char *context_text =
      usage_presenter_quota_status_text(has_data, stale, view.context);

  if (!*context_initialized ||
      strcmp(rendered_context, context_text) != 0) {
    lv_label_set_text(context, context_text);
    snprintf(rendered_context, rendered_context_cap, "%s", context_text);
    *context_initialized = true;
  }

  if (!*halo_initialized || *halo_visible != view.halo_active) {
    if (view.halo_active)
      lv_obj_remove_flag(halo, LV_OBJ_FLAG_HIDDEN);
    else
      lv_obj_add_flag(halo, LV_OBJ_FLAG_HIDDEN);
    *halo_visible = view.halo_active;
    *halo_initialized = true;
  }
}

static void refresh_header(quota_page *page, int64_t now_us) {
  bool stale = ui.stale || page->quota_stale;
  refresh_live_header(page->halo, page->context, &page->halo_initialized,
                      &page->halo_visible, &page->context_initialized,
                      page->rendered_context, sizeof page->rendered_context,
                      page->provider, page->has_data, stale, now_us);
}

static void refresh_tracker_header(tracker_page *page, int64_t now_us) {
  bool stale = ui.stale || page->quota_stale;
  refresh_live_header(page->halo, page->context, &page->halo_initialized,
                      &page->halo_visible, &page->context_initialized,
                      page->rendered_context, sizeof page->rendered_context,
                      page->provider, page->has_data, stale, now_us);
}

static bool apply_today_bar(quota_page *page,
                            const usage_card_view *quota) {
  usage_today_bar_view bar = {0};
  bool available = usage_live_build_today_bar(
      quota->pct, quota->has_pct, quota->delta_pct, quota->has_delta,
      VP_CONTENT_W, &bar);

  if (!available) {
    lv_obj_add_flag(page->baseline_fill, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(page->today_fill, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_remove_flag(page->baseline_fill, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(page->today_fill, LV_OBJ_FLAG_HIDDEN);
  }

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
    return available;
  }

  int marker_x = VP_SAFE_X + bar.marker_x - 1;
  if (marker_x < VP_SAFE_X) marker_x = VP_SAFE_X;
  if (marker_x > VP_SAFE_X + VP_CONTENT_W - 3)
    marker_x = VP_SAFE_X + VP_CONTENT_W - 3;
  lv_obj_set_x(page->marker, marker_x);
  lv_obj_remove_flag(page->marker, LV_OBJ_FLAG_HIDDEN);
  return available;
}

static void apply_quota(quota_page *page, const tk_tokens *tokens) {
  usage_quota_page_view view = {0};
  usage_presenter_build_quota_page(tokens, page->scope, &view);
  const usage_card_view *quota = &view.quota;
  page->has_data = quota->has_pct;
  page->quota_stale = quota->stale;
  lv_label_set_text(page->quota, quota->label);
  lv_label_set_text(page->percent, quota->pct_text);
  bool bar_available = apply_today_bar(page, quota);
  lv_label_set_text(page->today,
                    quota->has_delta && bar_available
                        ? quota->delta_text : "–");
  lv_label_set_text(page->reset, quota->reset_short_text);
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

/* Ritkärna för Max Tracker: 140 rutor (20 veckor x 7 ISO-veckodagar, index
 * = kolumn*7 + rad) plus 5 legend-swatchar i SAMMA lv_obj_t — statiskt,
 * ingen pixelbuffert, inga per-cell-objekt (AMOLED-minnesinvarianten).
 * "MAX"-etiketten är en vanlig lv_label, skapad en gång i
 * create_tracker_page. */
static void tracker_grid_draw(lv_event_t *e) {
  const tracker_page *pg = lv_event_get_user_data(e);
  lv_layer_t *layer = lv_event_get_layer(e);
  lv_area_t o;
  lv_obj_get_coords(lv_event_get_target(e), &o);

  for (int i = 0; i < TK_MT_DAYS; i++) {
    const tk_mt_day *d = &pg->data.days[i];
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.radius = 3;
    dsc.bg_opa = LV_OPA_COVER;
    if (d->pct >= 0) {
      tk_mt_rgb c = tk_mt_cell_rgb(pg->codex, d->pct);
      dsc.bg_color = lv_color_make(c.r, c.g, c.b);
    } else if (d->lvl >= 0) {
      tk_mt_rgb c = tk_mt_gray_rgb(d->lvl);
      dsc.bg_color = lv_color_make(c.r, c.g, c.b);
      dsc.border_width = 1;
      dsc.border_opa = LV_OPA_COVER;
      dsc.border_color = lv_color_hex(0x3d434d);
    } else {
      dsc.bg_color = lv_color_hex(pg->codex ? 0x0c0e13 : 0x0c0e11);
    }
    int wx = i / MT_ROWS, wy = i % MT_ROWS;
    lv_area_t a = { o.x1 + wx * MT_PITCH, o.y1 + wy * MT_PITCH,
                    o.x1 + wx * MT_PITCH + MT_CELL - 1,
                    o.y1 + wy * MT_PITCH + MT_CELL - 1 };
    lv_draw_rect(layer, &dsc, &a);
  }

  int legend_top = o.y1 + (MT_LEGEND_Y - MT_GRID_Y);
  int legend_right = o.x1 + MT_GRID_W;
  int block_x0 = legend_right - MT_LEGEND_LABEL_GAP - MT_LEGEND_LABEL_W -
                 MT_LEGEND_BLOCK_W;
  for (int i = 0; i < 5; i++) {
    tk_mt_rgb c = tk_mt_cell_rgb(pg->codex, TK_MT_LEGEND_PCTS[i]);
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.radius = 3;
    dsc.bg_opa = LV_OPA_COVER;
    dsc.bg_color = lv_color_make(c.r, c.g, c.b);
    int x1 = block_x0 + i * (MT_LEGEND_SWATCH + MT_LEGEND_GAP);
    lv_area_t a = { x1, legend_top, x1 + MT_LEGEND_SWATCH - 1,
                    legend_top + MT_LEGEND_SWATCH - 1 };
    lv_draw_rect(layer, &dsc, &a);
  }
}

static void create_tracker_page(tracker_page *page, int index, bool codex) {
  memset(page, 0, sizeof *page);
  page->provider = codex ? USAGE_PROVIDER_CODEX : USAGE_PROVIDER_CLAUDE;
  page->codex = codex;
  page->coding_streak_days = -1;
  for (int i = 0; i < TK_MT_DAYS; i++) {
    page->data.days[i].pct = -1;
    page->data.days[i].lvl = -1;
  }

  page->tile = new_tile(index);
  create_live_header_widgets(page->tile, page->provider, &page->halo,
                             &page->context);
  page->halo_initialized = true;

  lv_obj_t *eyebrow = label(page->tile, &plex_text_21, COL_MUTED,
                            VP_SAFE_X, MT_EYEBROW_Y, 240, 26);
  lv_obj_set_style_text_letter_space(eyebrow, 2, 0);
  lv_label_set_text(eyebrow, "MAX TRACKER");

  /* plan_label kan innehålla siffror ("MAX 20X"); plex_text_16 saknar
   * 0-9 (bara A-Z/mellanslag/ÅÄÖ). plex_ui_16 är SAMMA typsnitt/storlek
   * (IBM Plex Sans SemiBold 16 px) med bredare glyftäckning. */
  page->plan_badge = label(page->tile, &plex_ui_16, COL_MUTED,
                           298, MT_EYEBROW_Y, 160, 20);
  lv_obj_set_style_text_align(page->plan_badge, LV_TEXT_ALIGN_RIGHT, 0);
  lv_label_set_text(page->plan_badge, "");

  page->grid = bare(page->tile);
  lv_obj_set_pos(page->grid, MT_GRID_X, MT_GRID_Y);
  lv_obj_set_size(page->grid, MT_GRID_W, MT_DRAW_H);
  lv_obj_add_event_cb(page->grid, tracker_grid_draw, LV_EVENT_DRAW_MAIN,
                      page);

  lv_obj_t *max_label = label(page->tile, &plex_text_16, COL_MUTED,
                              MT_GRID_RIGHT - MT_LEGEND_LABEL_W,
                              MT_LEGEND_Y - 2, MT_LEGEND_LABEL_W, 16);
  lv_obj_set_style_text_align(max_label, LV_TEXT_ALIGN_RIGHT, 0);
  lv_label_set_text(max_label, "MAX");

  create_hairline(page->tile, MT_STAT_LINE_Y);

  static const char *const captions[4] = {
    "STREAK", "MAX WEEKS", "AVG PEAK", "MAX DAYS",
  };
  for (int i = 0; i < 4; i++) {
    int x = MT_GRID_X + (i * MT_GRID_W) / 4;
    /* -8 px gutter (samma marginal som RIGHT_STAT_X/RIGHT_STAT_W lämnar
     * mellan kvotsidornas kolumner) så "MAX WEEKS" aldrig rör vid
     * "AVG PEAK" — fyra jämnbreda kolumner, inte fyra sammanhängande. */
    lv_obj_t *caption = label(page->tile, &plex_text_16, COL_MUTED,
                              x, MT_STAT_LABEL_Y, MT_STAT_COL_W - 6, 16);
    lv_label_set_text(caption, captions[i]);

    page->stat_value[i] = label_auto(page->tile, &plex_num_38, COL_WHITE,
                                     x, MT_STAT_VALUE_Y);
    lv_label_set_text(page->stat_value[i], "–");
    page->stat_unit[i] = label_auto(page->tile, &plex_text_17, COL_MUTED,
                                    x, MT_STAT_VALUE_Y + 14);
    lv_label_set_text(page->stat_unit[i], "");
    lv_obj_add_flag(page->stat_unit[i], LV_OBJ_FLAG_HIDDEN);
  }

  create_pager(page->tile, index);
}

static void position_stat_unit(lv_obj_t *value_obj, lv_obj_t *unit_obj) {
  if (lv_label_get_text(unit_obj)[0] == '\0') {
    lv_obj_add_flag(unit_obj, LV_OBJ_FLAG_HIDDEN);
    return;
  }
  lv_obj_update_layout(value_obj);
  lv_obj_set_x(unit_obj, lv_obj_get_x(value_obj) +
                             lv_obj_get_width(value_obj) + 6);
  lv_obj_remove_flag(unit_obj, LV_OBJ_FLAG_HIDDEN);
}

static void apply_tracker_page(tracker_page *page, const tk_max_tracker *t) {
  const tk_mt_provider *src = page->codex ? &t->codex : &t->claude;
  page->data = *src;
  page->coding_streak_days = t->coding_streak_days;
  page->has_data = true;
  page->quota_stale = t->stale;

  lv_label_set_text(page->plan_badge,
                    src->has_plan ? src->plan_label : "");

  tk_mt_tile tiles[4];
  tk_mt_tiles(t, page->codex, tiles);
  for (int i = 0; i < 4; i++) {
    lv_label_set_text(page->stat_value[i], tiles[i].value);
    lv_label_set_text(page->stat_unit[i], tiles[i].unit);
    position_stat_unit(page->stat_value[i], page->stat_unit[i]);
  }

  refresh_tracker_header(page, ui.last_now_us);
  lv_obj_invalidate(page->grid);
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
  create_tracker_page(&ui.trackers[0], VIEW_TRACKER_CLAUDE, false);
  create_tracker_page(&ui.trackers[1], VIEW_TRACKER_CODEX, true);
  create_value_page();
  tk_agent_monitor_create(root);
}

void usage_screen_apply_tokens(const tk_tokens *tokens) {
  if (!tokens) return;
  ui.last_tokens = *tokens;
  for (int i = 0; i < 3; i++) apply_quota(&ui.quotas[i], tokens);
  usage_forecast_page_view forecasts = {0};
  usage_presenter_build_forecasts(tokens, &forecasts);
  for (int i = 0; i < 2; i++)
    apply_forecast_row(&ui.forecast_rows[i], &forecasts.rows[i]);
  apply_value(tokens);
}

void usage_screen_apply_max_tracker(const tk_max_tracker *t) {
  if (!t) return;
  for (int i = 0; i < 2; i++) apply_tracker_page(&ui.trackers[i], t);
}

void usage_screen_apply_agent(const tk_agent_snapshot *snapshot,
                              int64_t now_us) {
  if (!snapshot) return;
  ui.agent_snapshot = *snapshot;
  ui.agent_applied_at_us = now_us;
  ui.last_now_us = now_us;
  ui.has_agent_snapshot = true;
  for (int i = 0; i < 3; i++) refresh_header(&ui.quotas[i], now_us);
  for (int i = 0; i < 2; i++) refresh_tracker_header(&ui.trackers[i], now_us);
  tk_agent_monitor_apply(snapshot, now_us);
}

void usage_screen_tick(int64_t now_us) {
  ui.last_now_us = now_us;
  for (int i = 0; i < 3; i++) refresh_header(&ui.quotas[i], now_us);
  for (int i = 0; i < 2; i++) refresh_tracker_header(&ui.trackers[i], now_us);
  tk_agent_monitor_tick(now_us);
}

void usage_screen_set_stale(bool stale) {
  ui.stale = stale;
  for (int i = 0; i < 3; i++)
    refresh_header(&ui.quotas[i], ui.last_now_us);
  for (int i = 0; i < 2; i++)
    refresh_tracker_header(&ui.trackers[i], ui.last_now_us);
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
