#include "app_tokens.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "fmt_sv.h"
#include "ticker.h"
#include "torget.h"

/*
 * En vy, samma anatomi som Solelkollens vy 1 (eyebrow, tickande hero,
 * hårlinje, två statblock) — designsystemet är Torgets, inte appens.
 * Heron är dagens förbrukning i Mtok (miljoner tokens) med två fasta och
 * två strömmande decimaler: vid 3 Mtok/h rör svansen sig i läsbar takt,
 * precis som kronräknarens öressvans. Utan data: streck, aldrig nollor.
 *
 * Färgaccenten är terrakotta i stället för amber — varje app får en egen
 * accent på den gemensamma svarta botten, det är så launchern skiljer dem åt.
 */

extern const lv_font_t plex_num_146;
extern const lv_font_t plex_num_50;
extern const lv_font_t plex_num_38;
extern const lv_font_t plex_text_32;
extern const lv_font_t plex_text_21;
extern const lv_font_t plex_text_16;
extern const lv_font_t plex_text_17;
extern const lv_font_t plex_icon_64;

#define COL_ACCENT   lv_color_hex(0xD97757) /* terrakotta */
#define COL_WHITE    lv_color_hex(0xFFFFFF)
#define COL_LABEL    lv_color_hex(0x8994A5)
#define COL_FRAC     lv_color_hex(0x6B7788)
#define COL_TICK     lv_color_hex(0x3F4855)
#define COL_STALE    lv_color_hex(0x5C687B)
#define COL_HAIRLINE lv_color_hex(0x232D3B)

#define PAD_TOP 26
#define PAD_SIDE 24
#define PAD_BOTTOM 42

#define STALE_AFTER_US (120LL * 1000000LL)
#define TICK_EVERY_MS  100

static struct {
  lv_obj_t *eyebrow;
  lv_span_t *sp_whole, *sp_frac, *sp_tick;
  lv_obj_t *span_group; /* LVGL 9.5: set_text ritar INTE om — refresh krävs */
  lv_obj_t *sessions_value, *month_value;

  sg_ticker ticker; /* i Mtok — samma komponent som kronräknaren */
  int64_t last_success_us;
  bool has_data;
  bool is_stale;
} tk;

/* ---------------------------------------------------------------- helpers */

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  lv_obj_set_size(o, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  /* Icke klickbara: trycket ska falla igenom till appytan där långtrycket
   * (launchern) bor. */
  lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
  return o;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, lv_color_t color) {
  lv_obj_t *l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, color, 0);
  return l;
}

static void open_launcher(lv_event_t *e) {
  (void)e;
  torget_launcher_open();
}

/* ------------------------------------------------------------- tillstånd */

static void set_ticker_mtok(double mtok) {
  if (!tk.has_data) return;
  sv_ticker_str s;
  sv_ticker_split(mtok, &s);
  lv_span_set_text(tk.sp_whole, s.whole);
  char frac[8];
  snprintf(frac, sizeof frac, ",%s", s.frac);
  lv_span_set_text(tk.sp_frac, frac);
  lv_span_set_text(tk.sp_tick, s.tail);
  lv_spangroup_refresh(tk.span_group); /* LVGL 9.5-regeln */
}

void tokens_apply(const tk_tokens *t) {
  int64_t now = torget_now_us();
  tk.has_data = true;

  char buf[24];
  sv_group_ll(t->day_sessions, buf, sizeof buf);
  lv_label_set_text(tk.sessions_value, buf);
  sv_group_ll((long long)llround(t->month_tokens / 1e6), buf, sizeof buf);
  lv_label_set_text(tk.month_value, buf);

  /* Räknaren backar aldrig: vår extrapolering ligger som mest en hämt-
   * cykel före tjänsten (30 s à brinntakten). Ett tapp inom det bruset är
   * extrapoleringen som får komma ikapp — behåll vårt värde som bas. Ett
   * större tapp är på riktigt: midnatt nollar mätaren, precis som kron-
   * räknarens nya dygn. */
  double mtok = t->day_tokens / 1e6;
  double rate = t->day_tokens_per_hour / 1e6;
  double slack = rate / 120.0 + 0.001; /* en 30 s-cykel av takten, plus brus */
  double shown = sg_ticker_value(&tk.ticker, now);
  double base = (tk.ticker.has_data && mtok > 0.0
                 && mtok < shown && shown - mtok < slack)
                ? shown : mtok;
  sg_ticker_set(&tk.ticker, now, base, rate);

  tk.last_success_us = now;
  /* Brinner det tokens är någon vaken vid tangentbordet — håll skärmen i
   * dagsläge (Tokenmätarens motsvarighet till "solen är villkoret"). */
  if (t->day_tokens_per_hour > 0) torget_keep_awake();
}

static void set_stale(bool stale) {
  lv_obj_set_style_text_color(tk.eyebrow, stale ? COL_STALE : COL_LABEL, 0);
}

/* Appens puls: ticka Mtok mellan hämtningarna, tänd stale efter två minuter
 * utan färsk data. */
static void tick_cb(lv_timer_t *t) {
  (void)t;
  int64_t now = torget_now_us();

  if (tk.ticker.has_data)
    set_ticker_mtok(sg_ticker_value(&tk.ticker, now));

  bool stale = tk.has_data && (now - tk.last_success_us) > STALE_AFTER_US;
  if (stale != tk.is_stale) {
    tk.is_stale = stale;
    set_stale(stale);
  }
}

/* ------------------------------------------------------------------ bygge */

void tokens_net_start(void); /* net.c — finns bara i targetbygget */

static void tk_create(lv_obj_t *root) {
  memset(&tk, 0, sizeof tk);

  /* En enda "tile": hela ytan är klickbar så långtrycket når launchern
   * varifrån man än håller fingret — samma inputmodell som Solelkollens
   * tiles, utan tileview eftersom appen har en enda vy. */
  lv_obj_t *page = lv_obj_create(root);
  lv_obj_remove_style_all(page);
  lv_obj_set_size(page, 480, 480);
  lv_obj_set_style_pad_top(page, PAD_TOP, 0);
  lv_obj_set_style_pad_left(page, PAD_SIDE, 0);
  lv_obj_set_style_pad_right(page, PAD_SIDE, 0);
  lv_obj_set_style_pad_bottom(page, PAD_BOTTOM, 0);
  lv_obj_set_flex_flow(page, LV_FLEX_FLOW_COLUMN);
  lv_obj_remove_flag(page, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_event_cb(page, open_launcher, LV_EVENT_LONG_PRESSED, NULL);

  tk.eyebrow = label(page, &plex_text_21, COL_LABEL);
  lv_obj_set_style_text_letter_space(tk.eyebrow, 4, 0);
  lv_label_set_text(tk.eyebrow, "CLAUDE IDAG");

  /* Heroytan: yttre låda centrerar i flexutrymmet, inre rad bottenlinjerar
   * enheten mot den stora siffran — samma tvålådslösning som Solelkollen. */
  lv_obj_t *box = bare(page);
  lv_obj_set_width(box, LV_PCT(100));
  lv_obj_set_flex_grow(box, 1);
  lv_obj_set_flex_flow(box, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(box, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);

  lv_obj_t *h = bare(box);
  lv_obj_set_flex_flow(h, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(h, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END,
                        LV_FLEX_ALIGN_END);

  lv_obj_t *spans = lv_spangroup_create(h);
  tk.span_group = spans;
  lv_spangroup_set_align(spans, LV_TEXT_ALIGN_LEFT);
  lv_obj_remove_flag(spans, LV_OBJ_FLAG_CLICKABLE);
  tk.sp_whole = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(tk.sp_whole), &plex_num_146);
  lv_style_set_text_color(lv_span_get_style(tk.sp_whole), COL_WHITE);
  tk.sp_frac = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(tk.sp_frac), &plex_num_50);
  lv_style_set_text_color(lv_span_get_style(tk.sp_frac), COL_FRAC);
  tk.sp_tick = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(tk.sp_tick), &plex_num_50);
  lv_style_set_text_color(lv_span_get_style(tk.sp_tick), COL_TICK);

  lv_obj_t *unit = label(h, &plex_text_32, COL_LABEL);
  lv_label_set_text(unit, "Mtok");
  lv_obj_set_style_pad_left(unit, 11, 0);
  lv_obj_set_style_translate_y(unit, -18, 0);

  lv_obj_t *stats = bare(page);
  lv_obj_set_width(stats, LV_PCT(100));
  lv_obj_set_style_border_side(stats, LV_BORDER_SIDE_TOP, 0);
  lv_obj_set_style_border_width(stats, 1, 0);
  lv_obj_set_style_border_color(stats, COL_HAIRLINE, 0);
  lv_obj_set_style_pad_top(stats, 18, 0);
  lv_obj_set_flex_flow(stats, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(stats, 16, 0);

  /* Statblocken: samma anatomi som Solelkollens (16-etikett, 38-värde,
   * 17-enhet på optisk baslinje). */
  for (int i = 0; i < 2; i++) {
    lv_obj_t *side = bare(stats);
    lv_obj_set_flex_grow(side, 1);
    lv_obj_t *block = bare(side);
    lv_obj_set_flex_flow(block, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(block, 4, 0);

    lv_obj_t *l = label(block, &plex_text_16, COL_LABEL);
    lv_obj_set_style_text_letter_space(l, 2, 0);
    lv_label_set_text(l, i == 0 ? "SESSIONER" : "DENNA MÅNAD");

    lv_obj_t *row = bare(block);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END,
                          LV_FLEX_ALIGN_END);
    lv_obj_set_style_pad_column(row, 5, 0);

    lv_obj_t *value = label(row, &plex_num_38, COL_WHITE);
    lv_label_set_text(value, "–");
    if (i == 0) tk.sessions_value = value; else tk.month_value = value;

    lv_obj_t *u = label(row, &plex_text_17, COL_LABEL);
    lv_label_set_text(u, i == 0 ? "" : "Mtok");
    lv_obj_set_style_translate_y(u, -4, 0);
  }

  /* Utan data: streck, aldrig nollor. */
  lv_span_set_text(tk.sp_whole, "–");
  lv_span_set_text(tk.sp_frac, "");
  lv_span_set_text(tk.sp_tick, "");
  lv_spangroup_refresh(tk.span_group);

  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);

#ifdef ESP_PLATFORM
  /* Nätverket är appens: hämttasken väntar själv på torget_net_wait().
   * Simulatorn kompilerar inte net.c — den matar tokens_apply med fixturen. */
  tokens_net_start();
#endif
}

const torget_app_t tokens_app = {
  .api_version = TORGET_APP_API_VERSION,
  .name = "TOKENMÄTAREN",
  .icon = {
    /* Djup terrakottabrun platta, T-glyf, terrakottaprick — samma
     * proportioner som Solelkollens ikon, egen accent. */
    .font = &plex_icon_64,
    .glyph = "T",
    .plate_hex = 0x3A2114,
    .glyph_hex = 0xFFFFFF,
    .dot_hex = 0xD97757,
  },
  .create = tk_create,
  .enter = NULL,
  .leave = NULL,
};
