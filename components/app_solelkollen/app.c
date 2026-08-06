#include "app_solelkollen.h"

#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "fmt_sv.h"
#include "ticker.h"
#include "torget.h"

/*
 * Layoutsanningen bor i spec/ui-spec.md; den här filen är dess LVGL-
 * översättning, portad oförändrad från Solceller-repots ui/ui.c till
 * Torgets appkontrakt. Allt är panelpixlar på 480×480. Färgerna är
 * enhetspaletten (äkta svart botten, mörklägesambern #ff9f2f som enda
 * accent). Det som tillkom i porten: appen äger numera sin egen ticker,
 * sin stale-tröskel och sin 10 Hz-timer — det låg i värdlagren förr och
 * var rätt för en app, fel för tio (P25).
 */

extern const lv_font_t plex_num_146;
extern const lv_font_t plex_num_118;
extern const lv_font_t plex_num_50;
extern const lv_font_t plex_num_38;
extern const lv_font_t plex_text_32;
extern const lv_font_t plex_text_21;
extern const lv_font_t plex_text_16;
extern const lv_font_t plex_text_17;
extern const lv_font_t plex_icon_64;

#define COL_ACCENT   lv_color_hex(0xFF9F2F)
#define COL_WHITE    lv_color_hex(0xFFFFFF)
#define COL_LABEL    lv_color_hex(0x8994A5)
#define COL_FRAC     lv_color_hex(0x6B7788)
#define COL_TICK     lv_color_hex(0x3F4855)
#define COL_PAYLBL   lv_color_hex(0x5C687B)
#define COL_HAIRLINE lv_color_hex(0x232D3B)
#define COL_TRACK    lv_color_hex(0x1D2634)
#define COL_DOT_OFF  lv_color_hex(0x2B3442)
#define COL_LVL_LOW  lv_color_hex(0x6FC795)
#define COL_LVL_MID  lv_color_hex(0x8994A5)
#define COL_LVL_HIGH lv_color_hex(0xE08A63)

#define PAD_TOP 26
#define PAD_SIDE 24
#define PAD_BOTTOM 42

#define SK_VIEWS 4

/* Samma trösklar som webben: 120 s utan lyckad hämtning = stale. Tickern
 * uppdateras i ~10 Hz så svansdecimalen lever medan CPU:n sover. */
#define STALE_AFTER_US (120LL * 1000000LL)
#define TICK_EVERY_MS  100

static struct {
  lv_obj_t *tileview;
  lv_obj_t *dots[SK_VIEWS];
  lv_obj_t *eyebrows[SK_VIEWS];

  /* vy 1 */
  lv_span_t *sp_whole, *sp_frac, *sp_tick;
  lv_obj_t *span_group; /* LVGL 9.5: set_text ritar INTE om — refresh krävs */
  lv_obj_t *unit1, *w_value, *ore_value, *level_dot;
  /* vy 2 */
  lv_obj_t *year_value, *total_value;
  /* vy 3 */
  lv_obj_t *pct_value, *bar_fill, *pay_label;
  /* vy 4: Sverige (P23) */
  lv_obj_t *sve_value, *sve_day_value, *sve_share_value;

  sg_ticker ticker;
  int64_t last_success_us;
  bool has_data;
  bool is_stale;
} sk;

/* ---------------------------------------------------------------- helpers */

static void open_launcher(lv_event_t *e);

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  /* remove_style_all nollställer INTE default-storleken 100×50 — lådor ska
   * krama sitt innehåll, annars klipps barnen tyst (första renderingen:
   * statvärdena klipptes under 50-pixellinjen). */
  lv_obj_set_size(o, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  /* Icke klickbara: trycket ska falla igenom layoutlådorna ner till TILEN,
   * så att långtrycket (launchern) fungerar var man än håller fingret. */
  lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
  return o;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, lv_color_t color) {
  lv_obj_t *l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, color, 0);
  return l;
}

static lv_obj_t *eyebrow(lv_obj_t *parent, const char *text) {
  lv_obj_t *l = label(parent, &plex_text_21, COL_LABEL);
  lv_obj_set_style_text_letter_space(l, 4, 0); /* 0.17 em av 21 px */
  lv_label_set_text(l, text);
  return l;
}

static lv_obj_t *view(int col, const char *eyebrow_text, int idx) {
  lv_obj_t *tile = lv_tileview_add_tile(
    sk.tileview, col, 0,
    col == 0 ? LV_DIR_RIGHT : (col == SK_VIEWS - 1 ? LV_DIR_LEFT : LV_DIR_HOR));
  lv_obj_set_style_pad_top(tile, PAD_TOP, 0);
  lv_obj_set_style_pad_left(tile, PAD_SIDE, 0);
  lv_obj_set_style_pad_right(tile, PAD_SIDE, 0);
  lv_obj_set_style_pad_bottom(tile, PAD_BOTTOM, 0);
  lv_obj_set_flex_flow(tile, LV_FLEX_FLOW_COLUMN);
  lv_obj_remove_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
  /* bare()-lådorna är icke klickbara, så trycket landar här på tilen. */
  lv_obj_add_event_cb(tile, open_launcher, LV_EVENT_LONG_PRESSED, NULL);
  sk.eyebrows[idx] = eyebrow(tile, eyebrow_text);
  return tile;
}

/* Heroytan: en yttre låda som suger upp flexutrymmet mellan eyebrow och
 * statraden och centrerar vertikalt, med en inre RAD vars barn bottenlinjeras
 * så den lilla enheten sitter nära den stora siffrans baslinje. Två lådor
 * eftersom en och samma inte kan både centrera-i-utrymme och bottenlinjera. */
static lv_obj_t *hero_row(lv_obj_t *tile) {
  lv_obj_t *box = bare(tile);
  lv_obj_set_width(box, LV_PCT(100));
  lv_obj_set_flex_grow(box, 1);
  lv_obj_set_flex_flow(box, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(box, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);

  lv_obj_t *row = bare(box);
  lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END,
                        LV_FLEX_ALIGN_END);
  return row;
}

static lv_obj_t *stat_block(lv_obj_t *parent, const char *label_text,
                            lv_obj_t **value_out, const char *unit_text) {
  lv_obj_t *block = bare(parent);
  lv_obj_set_flex_flow(block, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(block, 4, 0);

  lv_obj_t *l = label(block, &plex_text_16, COL_LABEL);
  lv_obj_set_style_text_letter_space(l, 2, 0); /* 0.14 em av 16 px */
  lv_label_set_text(l, label_text);

  lv_obj_t *row = bare(block);
  lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END,
                        LV_FLEX_ALIGN_END);
  lv_obj_set_style_pad_column(row, 5, 0);

  *value_out = label(row, &plex_num_38, COL_WHITE);
  lv_label_set_text(*value_out, "–");
  lv_obj_t *u = label(row, &plex_text_17, COL_LABEL);
  lv_label_set_text(u, unit_text);
  lv_obj_set_style_translate_y(u, -4, 0); /* optisk baslinje mot 38-pixlarna */
  return row;
}

static void open_launcher(lv_event_t *e) {
  (void)e;
  torget_launcher_open();
}

static void tile_changed(lv_event_t *e) {
  (void)e;
  lv_obj_t *active = lv_tileview_get_tile_active(sk.tileview);
  int idx = active ? (lv_obj_get_x(active) / 480) : 0;
  for (int i = 0; i < SK_VIEWS; i++)
    lv_obj_set_style_bg_color(sk.dots[i], i == idx ? COL_ACCENT : COL_DOT_OFF, 0);
}

/* ------------------------------------------------------------- tillstånd */

static void set_no_data(void) {
  sk.has_data = false;
  lv_span_set_text(sk.sp_whole, "–");
  lv_span_set_text(sk.sp_frac, "");
  lv_span_set_text(sk.sp_tick, "");
  lv_spangroup_refresh(sk.span_group);
  lv_label_set_text(sk.w_value, "–");
  lv_label_set_text(sk.ore_value, "–");
  lv_label_set_text(sk.year_value, "–");
  lv_label_set_text(sk.total_value, "–");
  lv_label_set_text(sk.pct_value, "–");
  lv_label_set_text(sk.pay_label, "");
  lv_obj_set_width(sk.bar_fill, 0);
  /* Sverige-vyn har egen livscykel men samma nolläge: streck, aldrig nollor. */
  lv_label_set_text(sk.sve_value, "–");
  lv_label_set_text(sk.sve_day_value, "–");
  lv_label_set_text(sk.sve_share_value, "–");
}

static void set_ticker_kr(double kr) {
  if (!sk.has_data) return;
  sv_ticker_str s;
  sv_ticker_split(kr, &s);
  lv_span_set_text(sk.sp_whole, s.whole);
  char frac[8];
  snprintf(frac, sizeof frac, ",%s", s.frac);
  lv_span_set_text(sk.sp_frac, frac);
  lv_span_set_text(sk.sp_tick, s.tail);
  /* LVGL 9.5: set_text uppdaterar bara strängen — utan refresh ritas svansen
   * bara om när något ANNAT tvingar omritning (pixeldriften, en gång per
   * minut). Diagnosen: mätsonden loggade rullande värden mot en stum skärm. */
  lv_spangroup_refresh(sk.span_group);
}

static void set_stale(bool stale) {
  lv_color_t c = stale ? COL_PAYLBL : COL_LABEL;
  /* Vy 4 (Sverige) undantas: settlement har sin egen, mycket latare kadens
   * och vyn daterar sig själv med AVRÄKNAT-datumet. */
  for (int i = 0; i < 3; i++)
    if (sk.eyebrows[i]) lv_obj_set_style_text_color(sk.eyebrows[i], c, 0);
}

void solelkollen_apply_glance(const sg_glance *g) {
  int64_t now = torget_now_us();
  sk.has_data = true;

  /* 64, inte 48: "<belopp> kr av <belopp> kr" av två maximala grupperingar
   * ryms först då bevisligen, och GCC på targetet räknar värsta fallet. */
  char buf[64], grouped[24], grouped2[24];

  sv_group_ll(g->w, buf, sizeof buf);
  lv_label_set_text(sk.w_value, buf);
  sv_group_ll(g->ore, buf, sizeof buf);
  lv_label_set_text(sk.ore_value, buf);
  lv_obj_set_style_bg_color(
    sk.level_dot,
    g->level < 0 ? COL_LVL_LOW : (g->level > 0 ? COL_LVL_HIGH : COL_LVL_MID), 0);

  sv_group_ll(g->year_kr, buf, sizeof buf);
  lv_label_set_text(sk.year_value, buf);
  sv_group_ll(g->total_kr, buf, sizeof buf);
  lv_label_set_text(sk.total_value, buf);

  sv_pct_1(g->payback_pct, buf, sizeof buf);
  lv_label_set_text(sk.pct_value, buf);
  double pct = g->payback_pct;
  if (pct > 100) pct = 100;
  if (pct < 0) pct = 0;
  lv_obj_set_width(sk.bar_fill, (int32_t)(432.0 * pct / 100.0));

  sv_group_ll(g->total_kr, grouped, sizeof grouped);
  sv_group_ll(g->installation_kr, grouped2, sizeof grouped2);
  snprintf(buf, sizeof buf, "%s kr av %s kr", grouped, grouped2);
  lv_label_set_text(sk.pay_label, buf);

  /* Servern presentationsavrundar till hela ören; vår lokala extrapolering
   * ligger då ofta bråkdelar av ett öre FÖRE. Att snäppa bakåt inom det
   * bruset bryter regeln att räknare aldrig backar — behåll vårt värde som
   * bas och låt servern komma ikapp. Ett tapp större än ett öre är en äkta
   * korrigering och snäpps som vanligt (liksom ett nytt dygn: kr == 0). */
  double shown = sg_ticker_value(&sk.ticker, now);
  double base = (sk.ticker.has_data && g->kr > 0.0
                 && g->kr < shown && shown - g->kr < 0.011)
                ? shown : g->kr;
  sg_ticker_set(&sk.ticker, now, base, g->kr_per_hour);

  sk.last_success_us = now;
  /* Solen är villkoret, inte klockan: produktion håller skärmen i dagsläge. */
  if (g->w > 0) torget_keep_awake();
}

void solelkollen_apply_sverige(const sg_sverige *s) {
  char buf[24];

  sv_pct_1(s->day_gwh, buf, sizeof buf); /* generisk "en decimal, sv-SE" */
  lv_label_set_text(sk.sve_value, buf);

  sv_day_short(s->day, buf, sizeof buf);
  lv_label_set_text(sk.sve_day_value, buf);

  if (s->has_share) {
    sv_pct_1(s->share_pct, buf, sizeof buf);
    lv_label_set_text(sk.sve_share_value, buf);
  } else {
    lv_label_set_text(sk.sve_share_value, "–");
  }
}

void solelkollen_show_view(int idx) {
  if (idx < 0) idx = 0;
  if (idx > SK_VIEWS - 1) idx = SK_VIEWS - 1;
  lv_obj_set_tile_id(sk.tileview, idx, 0, LV_ANIM_OFF);
  for (int i = 0; i < SK_VIEWS; i++)
    lv_obj_set_style_bg_color(sk.dots[i], i == idx ? COL_ACCENT : COL_DOT_OFF, 0);
}

/* Appens egen puls: tickern extrapolerar mellan hämtningarna och stale-
 * markeringen tänds efter två minuter utan färsk data — exakt det värdlagren
 * gjorde åt Solelkollen förr, nu appens eget ansvar. Kör i LVGL-tasken. */
static void tick_cb(lv_timer_t *t) {
  (void)t;
  int64_t now = torget_now_us();

  if (sk.ticker.has_data)
    set_ticker_kr(sg_ticker_value(&sk.ticker, now));

  bool stale = sk.has_data && (now - sk.last_success_us) > STALE_AFTER_US;
  if (stale != sk.is_stale) {
    sk.is_stale = stale;
    set_stale(stale);
  }
}

/* ------------------------------------------------------------------ bygge */

void solelkollen_net_start(void); /* net.c — finns bara i targetbygget */

static void sk_create(lv_obj_t *root) {
  memset(&sk, 0, sizeof sk);

  sk.tileview = lv_tileview_create(root);
  lv_obj_set_size(sk.tileview, 480, 480);
  lv_obj_set_style_bg_opa(sk.tileview, LV_OPA_TRANSP, 0);
  lv_obj_set_scrollbar_mode(sk.tileview, LV_SCROLLBAR_MODE_OFF);
  lv_obj_add_event_cb(sk.tileview, tile_changed, LV_EVENT_VALUE_CHANGED, NULL);

  /* --- vy 1: nu --------------------------------------------------------- */
  lv_obj_t *t1 = view(0, "GETT IDAG", 0);

  lv_obj_t *h1 = hero_row(t1);
  lv_obj_t *spans = lv_spangroup_create(h1);
  sk.span_group = spans;
  lv_spangroup_set_align(spans, LV_TEXT_ALIGN_LEFT);
  lv_obj_remove_flag(spans, LV_OBJ_FLAG_CLICKABLE); /* trycket ska nå tilen */
  sk.sp_whole = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(sk.sp_whole), &plex_num_146);
  lv_style_set_text_color(lv_span_get_style(sk.sp_whole), COL_WHITE);
  sk.sp_frac = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(sk.sp_frac), &plex_num_50);
  lv_style_set_text_color(lv_span_get_style(sk.sp_frac), COL_FRAC);
  sk.sp_tick = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(sk.sp_tick), &plex_num_50);
  lv_style_set_text_color(lv_span_get_style(sk.sp_tick), COL_TICK);

  sk.unit1 = label(h1, &plex_text_32, COL_LABEL);
  lv_label_set_text(sk.unit1, "kr");
  lv_obj_set_style_pad_left(sk.unit1, 11, 0);
  lv_obj_set_style_translate_y(sk.unit1, -18, 0);
  lv_obj_set_flex_align(h1, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END,
                        LV_FLEX_ALIGN_END);

  lv_obj_t *stats = bare(t1);
  lv_obj_set_width(stats, LV_PCT(100));
  lv_obj_set_style_border_side(stats, LV_BORDER_SIDE_TOP, 0);
  lv_obj_set_style_border_width(stats, 1, 0);
  lv_obj_set_style_border_color(stats, COL_HAIRLINE, 0);
  lv_obj_set_style_pad_top(stats, 18, 0);
  lv_obj_set_flex_flow(stats, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(stats, 16, 0);

  lv_obj_t *left = bare(stats);
  lv_obj_set_flex_grow(left, 1);
  stat_block(left, "EFFEKT", &sk.w_value, "W");

  lv_obj_t *right = bare(stats);
  lv_obj_set_flex_grow(right, 1);
  lv_obj_t *ore_row = stat_block(right, "ELPRIS", &sk.ore_value, "öre");
  sk.level_dot = bare(ore_row);
  lv_obj_set_size(sk.level_dot, 11, 11);
  lv_obj_set_style_radius(sk.level_dot, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(sk.level_dot, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(sk.level_dot, COL_LVL_MID, 0);
  lv_obj_set_style_translate_y(sk.level_dot, -12, 0);

  /* --- vy 2: året -------------------------------------------------------- */
  lv_obj_t *t2 = view(1, "GETT I ÅR", 1);
  lv_obj_t *h2 = hero_row(t2);
  lv_obj_set_flex_align(h2, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_END);
  sk.year_value = label(h2, &plex_num_118, COL_WHITE);
  lv_label_set_text(sk.year_value, "–");
  lv_obj_t *unit2 = label(h2, &plex_text_32, COL_LABEL);
  lv_label_set_text(unit2, "kr");
  lv_obj_set_style_pad_left(unit2, 11, 0);
  lv_obj_set_style_translate_y(unit2, -14, 0);

  lv_obj_t *stats2 = bare(t2);
  lv_obj_set_width(stats2, LV_PCT(100));
  lv_obj_set_style_border_side(stats2, LV_BORDER_SIDE_TOP, 0);
  lv_obj_set_style_border_width(stats2, 1, 0);
  lv_obj_set_style_border_color(stats2, COL_HAIRLINE, 0);
  lv_obj_set_style_pad_top(stats2, 18, 0);
  stat_block(stats2, "SEDAN START", &sk.total_value, "kr");

  /* --- vy 3: hela bågen -------------------------------------------------- */
  lv_obj_t *t3 = view(2, "ÅTERBETALT", 2);
  lv_obj_t *h3 = hero_row(t3);
  lv_obj_set_flex_align(h3, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_END);
  sk.pct_value = label(h3, &plex_num_146, COL_WHITE);
  lv_label_set_text(sk.pct_value, "–");
  lv_obj_t *unit3 = label(h3, &plex_text_32, COL_LABEL);
  lv_label_set_text(unit3, "%");
  lv_obj_set_style_pad_left(unit3, 11, 0);
  lv_obj_set_style_translate_y(unit3, -18, 0);

  lv_obj_t *pay = bare(t3);
  lv_obj_set_width(pay, LV_PCT(100));
  lv_obj_set_flex_flow(pay, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(pay, 7, 0);

  lv_obj_t *track = bare(pay);
  lv_obj_set_size(track, LV_PCT(100), 5);
  lv_obj_set_style_radius(track, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(track, COL_TRACK, 0);
  sk.bar_fill = bare(track);
  lv_obj_set_size(sk.bar_fill, 0, 5);
  lv_obj_set_style_radius(sk.bar_fill, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(sk.bar_fill, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(sk.bar_fill, COL_ACCENT, 0);

  sk.pay_label = label(pay, &plex_text_17, COL_PAYLBL);
  lv_label_set_text(sk.pay_label, "");

  /* --- vy 4: Sverige (P23) ----------------------------------------------- */
  lv_obj_t *t4 = view(3, "SVERIGE", 3);
  lv_obj_t *h4 = hero_row(t4);
  lv_obj_set_flex_align(h4, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_END);
  sk.sve_value = label(h4, &plex_num_146, COL_WHITE);
  lv_label_set_text(sk.sve_value, "–");
  lv_obj_t *unit4 = label(h4, &plex_text_32, COL_LABEL);
  lv_label_set_text(unit4, "GWh");
  lv_obj_set_style_pad_left(unit4, 11, 0);
  lv_obj_set_style_translate_y(unit4, -18, 0);

  lv_obj_t *stats4 = bare(t4);
  lv_obj_set_width(stats4, LV_PCT(100));
  lv_obj_set_style_border_side(stats4, LV_BORDER_SIDE_TOP, 0);
  lv_obj_set_style_border_width(stats4, 1, 0);
  lv_obj_set_style_border_color(stats4, COL_HAIRLINE, 0);
  lv_obj_set_style_pad_top(stats4, 18, 0);
  lv_obj_set_flex_flow(stats4, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(stats4, 16, 0);

  lv_obj_t *left4 = bare(stats4);
  lv_obj_set_flex_grow(left4, 1);
  /* "4 aug" är ett statvärde med bokstäver: num_38 bär gemener sedan P23,
   * och enhetsplatsen lämnas tom. */
  stat_block(left4, "AVRÄKNAT", &sk.sve_day_value, "");

  lv_obj_t *right4 = bare(stats4);
  lv_obj_set_flex_grow(right4, 1);
  stat_block(right4, "ANDEL AV ELEN", &sk.sve_share_value, "%");

  /* --- prickarna ---------------------------------------------------------- */
  lv_obj_t *dots_box = bare(root);
  lv_obj_align(dots_box, LV_ALIGN_BOTTOM_MID, 0, -15);
  lv_obj_set_flex_flow(dots_box, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(dots_box, 7, 0);
  for (int i = 0; i < SK_VIEWS; i++) {
    sk.dots[i] = bare(dots_box);
    lv_obj_set_size(sk.dots[i], 6, 6);
    lv_obj_set_style_radius(sk.dots[i], LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(sk.dots[i], LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(sk.dots[i], i == 0 ? COL_ACCENT : COL_DOT_OFF, 0);
  }

  set_no_data();
  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);

#ifdef ESP_PLATFORM
  /* Nätverket är appens (P25-krav 2): hämttasken startar här, väntar själv
   * på torget_net_wait() och matar apply-funktionerna under UI-låset.
   * Simulatorn kompilerar inte net.c — den matar fixtures i stället. */
  solelkollen_net_start();
#endif
}

const torget_app_t solelkollen_app = {
  .api_version = TORGET_APP_API_VERSION,
  .name = "SOLELKOLLEN",
  .icon = {
    /* Märkesblå platta, S:et ur loggan, amber-pricken — samma ikon som
     * bänkens (proportionerna: 96-platta, radie 22). */
    .font = &plex_icon_64,
    .glyph = "S",
    .plate_hex = 0x10233A,
    .glyph_hex = 0xFFFFFF,
    .dot_hex = 0xFF9F2F,
  },
  .create = sk_create,
  .enter = NULL,
  .leave = NULL,
};
