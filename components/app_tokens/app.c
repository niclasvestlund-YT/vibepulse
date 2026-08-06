#include "app_tokens.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "fmt_sv.h"
#include "agent_monitor.h"
#include "agent_usage.h"
#include "ticker.h"
#include "torget.h"

#ifdef ESP_PLATFORM
#include "secrets.h"
#endif

/*
 * Tre vyer, samma anatomi som Solelkollens (eyebrow, hero, hårlinje, två
 * statblock) — designsystemet är Torgets, inte appens.
 *
 * Vy 1 "CLAUDE JUST NU" och vy 2 "CODEX JUST NU" är Clawdmeter-tanken:
 * hur nära taket är respektive prenumeration? Hero = sessionsfönstret i
 * procent (finns inget sessionsfönster på planen, som Codex prolite, visas
 * veckofönstret i heron i stället), statraden = när fönstret nollas +
 * veckofönstret. Inga tickande tal här — taket rör sig i tjänstens takt
 * och en hittad interpolering vore en lögn.
 *
 * Vy 3 "CLAUDE IDAG" är volymen: dagens Mtok som tickande hero (samma
 * tickerkomponent som kronräknaren) + sessioner och månadssumma.
 *
 * Färgaccenten är terrakotta — varje app får en egen accent på den
 * gemensamma svarta botten. Utan data: streck, aldrig nollor.
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

#define COL_ACCENT   lv_color_hex(0xD97757) /* terrakotta */
#define COL_WHITE    lv_color_hex(0xFFFFFF)
#define COL_LABEL    lv_color_hex(0x8994A5)
#define COL_FRAC     lv_color_hex(0x6B7788)
#define COL_TICK     lv_color_hex(0x3F4855)
#define COL_STALE    lv_color_hex(0x5C687B)
#define COL_HAIRLINE lv_color_hex(0x232D3B)
#define COL_DOT_OFF  lv_color_hex(0x2B3442)
#define COL_TRACK    lv_color_hex(0x1D2634)
/* Taknivåerna, samma familj som elprispricken: grönt är lugnt, amber är
 * "håll ett öga", varmrött är "taket är nära". Trösklar: 60 och 85 %. */
#define COL_LVL_LOW  lv_color_hex(0x6FC795)
#define COL_LVL_MID  lv_color_hex(0xFF9F2F)
#define COL_LVL_HIGH lv_color_hex(0xE0635B)

#define PAD_TOP 26
#define PAD_SIDE 24
#define PAD_BOTTOM 42

#define TK_VIEWS 3

#define STALE_AFTER_US (120LL * 1000000LL)
#define TICK_EVERY_MS  100

/* En takvy per leverantör: hero-%, nollas-om, vecko-% och (Claude) tyngsta
 * modellens vecko-%, var och en med sin progressbar — usage-panelens språk.
 * *_fill är barfyllnaden; NULL där fönstret inte finns. */
typedef struct {
  lv_obj_t *pct_value, *reset_value, *week_value, *model_value;
  lv_obj_t *hero_fill, *week_fill, *model_fill;
  lv_obj_t *week_block;  /* hela VECKAN-cellen — döljs när heron ÄR veckan */
  lv_obj_t *model_block; /* hela FABLE-cellen — döljs när källan saknas */

  /* NOLLAS OM räknas ner lokalt mellan hämtningarna (tid är tid — samma
   * ärliga extrapolering som kronräknaren): snapshoten + när den togs. */
  tk_limit hero_snap;
  int64_t snap_us;
  int shown_min; /* senast ritade minutvärdet — rita bara vid byte */
} tk_limit_view;

static struct {
  lv_obj_t *tileview;
  lv_obj_t *dots[TK_VIEWS];
  lv_obj_t *eyebrows[TK_VIEWS];

  /* vy 1-2: taken (Claude respektive Codex) */
  tk_limit_view claude, codex;
  /* vy 3: volymen */
  lv_obj_t *live_dot; /* andas när tokens brinner — mätarens hjärtslag */
  lv_span_t *sp_whole, *sp_frac;
  lv_obj_t *span_group; /* LVGL 9.5: set_text ritar INTE om — refresh krävs */
  lv_obj_t *sessions_value, *month_value;

  sg_ticker ticker; /* i Mtok — samma komponent som kronräknaren */
  int64_t last_success_us;
  bool has_data;
  bool is_stale;
} tk;

/* ---------------------------------------------------------------- helpers */

static void open_launcher(lv_event_t *e);

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  lv_obj_set_size(o, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  /* Icke klickbara: trycket ska falla igenom till tilen där långtrycket
   * (launchern) bor — samma inputmodell som Solelkollen. */
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

static void tile_changed(lv_event_t *e) {
  (void)e;
  lv_obj_t *active = lv_tileview_get_tile_active(tk.tileview);
  int idx = active ? (lv_obj_get_x(active) / 480) : 0;
  for (int i = 0; i < TK_VIEWS; i++)
    lv_obj_set_style_bg_color(tk.dots[i], i == idx ? COL_ACCENT : COL_DOT_OFF, 0);
}

static lv_obj_t *view(int col, const char *eyebrow_text, int idx) {
  lv_obj_t *tile = lv_tileview_add_tile(
    tk.tileview, col, 0,
    col == 0 ? LV_DIR_RIGHT : (col == TK_VIEWS - 1 ? LV_DIR_LEFT : LV_DIR_HOR));
  lv_obj_set_style_pad_top(tile, PAD_TOP, 0);
  lv_obj_set_style_pad_left(tile, PAD_SIDE, 0);
  lv_obj_set_style_pad_right(tile, PAD_SIDE, 0);
  lv_obj_set_style_pad_bottom(tile, PAD_BOTTOM, 0);
  lv_obj_set_flex_flow(tile, LV_FLEX_FLOW_COLUMN);
  lv_obj_remove_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_event_cb(tile, open_launcher, LV_EVENT_LONG_PRESSED, NULL);
  lv_obj_t *l = label(tile, &plex_text_21, COL_LABEL);
  lv_obj_set_style_text_letter_space(l, 4, 0);
  lv_label_set_text(l, eyebrow_text);
  tk.eyebrows[idx] = l;
  return tile;
}

/* Heroytan: yttre låda centrerar i flexutrymmet, inre rad bottenlinjerar
 * enheten mot den stora siffran — samma tvålådslösning som Solelkollen. */
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

/* Ett statblock, valfritt med en liten progressbar under värdet (usage-
 * panelens rad-språk): fill_out != NULL skapar 120×4-baren. Returnerar
 * CELLEN (flex-barnet) så en vy kan dölja hela blocket. */
#define STAT_BAR_W 120

static lv_obj_t *stat_block(lv_obj_t *parent, const char *label_text,
                            lv_obj_t **value_out, const char *unit_text,
                            lv_obj_t **fill_out) {
  lv_obj_t *side = bare(parent);
  lv_obj_set_flex_grow(side, 1);
  lv_obj_t *block = bare(side);
  lv_obj_set_flex_flow(block, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(block, 4, 0);

  lv_obj_t *l = label(block, &plex_text_16, COL_LABEL);
  lv_obj_set_style_text_letter_space(l, 2, 0);
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
  lv_obj_set_style_translate_y(u, -4, 0);

  if (fill_out) {
    lv_obj_t *track = bare(block);
    lv_obj_set_size(track, STAT_BAR_W, 4);
    lv_obj_set_style_radius(track, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(track, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(track, COL_TRACK, 0);
    *fill_out = bare(track);
    lv_obj_set_size(*fill_out, 0, 4);
    lv_obj_set_style_radius(*fill_out, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(*fill_out, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(*fill_out, COL_LVL_LOW, 0);
  }
  return side;
}

/* --------------------------------------------------------------- animering */

/* Barfyllnaderna rullar mjukt till sitt nya läge när data landar — ett
 * hopp skvallrar om en maskin, en rullning känns som en mätare. Färgen
 * följer nivån: grönt → amber → varmrött. */
static void bar_anim_cb(void *obj, int32_t w) { lv_obj_set_width(obj, w); }

static void bar_set(lv_obj_t *fill, int track_w, const tk_limit *lim) {
  if (!fill) return;
  int target = 0;
  lv_color_t c = COL_LVL_LOW;
  if (lim && lim->has_pct) {
    double p = lim->pct;
    if (p < 0) p = 0;
    if (p > 100) p = 100;
    target = (int)(track_w * p / 100.0);
    c = p >= 85.0 ? COL_LVL_HIGH : (p >= 60.0 ? COL_LVL_MID : COL_LVL_LOW);
  }
  lv_obj_set_style_bg_color(fill, c, 0);

  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, fill);
  lv_anim_set_values(&a, lv_obj_get_width(fill), target);
  lv_anim_set_duration(&a, 600);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
  lv_anim_set_exec_cb(&a, bar_anim_cb);
  lv_anim_start(&a);
}

/* Samma nivåfärg på heropocenten — vit så länge det är lugnt, sedan
 * amber och varmrött när taket närmar sig. */
static lv_color_t pct_color(const tk_limit *lim) {
  if (!lim->has_pct || lim->pct < 60.0) return COL_WHITE;
  return lim->pct >= 85.0 ? COL_LVL_HIGH : COL_LVL_MID;
}

/* Anda-pulsen: en terrakottaprick som mjukt tonar upp och ner så länge
 * tokens brinner — mätarens hjärtslag. Syns inte alls vid paus. */
static void breath_anim_cb(void *obj, int32_t v) {
  lv_obj_set_style_opa(obj, (lv_opa_t)v, 0);
}

static void breath_start(lv_obj_t *dot) {
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, dot);
  lv_anim_set_values(&a, 40, 255);
  lv_anim_set_duration(&a, 1100);
  lv_anim_set_playback_duration(&a, 1100);
  lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
  lv_anim_set_exec_cb(&a, breath_anim_cb);
  lv_anim_start(&a);
}

static lv_obj_t *stats_row(lv_obj_t *tile) {
  lv_obj_t *stats = bare(tile);
  lv_obj_set_width(stats, LV_PCT(100));
  lv_obj_set_style_border_side(stats, LV_BORDER_SIDE_TOP, 0);
  lv_obj_set_style_border_width(stats, 1, 0);
  lv_obj_set_style_border_color(stats, COL_HAIRLINE, 0);
  lv_obj_set_style_pad_top(stats, 18, 0);
  lv_obj_set_flex_flow(stats, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(stats, 16, 0);
  return stats;
}

/* "134 min" → "2 h 14"; under timmen "34 min". Kompakt med flit: Claude-vyn
 * har tre statblock på raden och "min"-suffixet ryms bara i entimmarsfallet.
 * num_38 bär gemener och mellanslag sedan P23. */
static void fmt_reset(int minutes, char *out, size_t cap) {
  if (minutes >= 60)
    snprintf(out, cap, "%d h %d", minutes / 60, minutes % 60);
  else
    snprintf(out, cap, "%d min", minutes);
}

/* ------------------------------------------------------------- tillstånd */

/* Två decimaler, inte kronräknarens 2+2: en Mtok-hero med tresiffrig
 * heldel OCH fyra decimaler klippte enheten vid skärmkanten (hittat på
 * foto, reproducerat i bänken). 0,01 Mtok = 10 000 tokens per steg —
 * svansen lever ändå i allt utom total stiltje. */
static void set_ticker_mtok(double mtok) {
  if (!tk.has_data) return;
  sv_ticker_str s;
  sv_ticker_split(mtok, &s);
  lv_span_set_text(tk.sp_whole, s.whole);
  char frac[8];
  snprintf(frac, sizeof frac, ",%s", s.frac);
  lv_span_set_text(tk.sp_frac, frac);
  lv_spangroup_refresh(tk.span_group); /* LVGL 9.5-regeln */
}

/* En leverantörs takvy. Heron är VECKOFÖNSTRET — det är veckobudgeten som
 * är den knappa resursen (Niclas prioritering vid designgranskningen);
 * 5-timmarsfönstret självläker på timmar och bor i statraden i stället
 * (SESSIONEN-blocket, dolt på planer som saknar fönstret, som Codex
 * prolite). Saknas veckodata faller heron tillbaka på sessionen.
 * null = streck, alltid. */
static void apply_limits(tk_limit_view *v, const tk_limit *session,
                         const tk_limit *week, const tk_limit *model_week) {
  char buf[24];
  const tk_limit *hero = week->has_pct ? week : session;
  bool show_session_stat = week->has_pct && session->has_pct;

  if (hero->has_pct) {
    sv_pct_1(hero->pct, buf, sizeof buf);
    lv_label_set_text(v->pct_value, buf);
  } else {
    lv_label_set_text(v->pct_value, "–");
  }
  lv_obj_set_style_text_color(v->pct_value, pct_color(hero), 0);
  bar_set(v->hero_fill, 432, hero->has_pct ? hero : NULL);
  v->hero_snap = *hero;
  v->snap_us = torget_now_us();
  v->shown_min = -1; /* tvinga omritning */
  if (hero->has_reset) {
    fmt_reset(hero->reset_min, buf, sizeof buf);
    lv_label_set_text(v->reset_value, buf);
    v->shown_min = hero->reset_min;
  } else {
    lv_label_set_text(v->reset_value, "–");
  }
  /* SESSIONEN-blocket (5h-fönstret) visas bara när båda fönstren finns —
   * annars vore det antingen herons siffra en gång till eller ett streck. */
  if (show_session_stat) {
    lv_obj_remove_flag(v->week_block, LV_OBJ_FLAG_HIDDEN);
    sv_pct_1(session->pct, buf, sizeof buf);
    lv_label_set_text(v->week_value, buf);
    bar_set(v->week_fill, STAT_BAR_W, session);
  } else {
    lv_obj_add_flag(v->week_block, LV_OBJ_FLAG_HIDDEN);
  }
  /* FABLE-cellen: API-headrarna bär (ännu) ingen per-modell-vecka — ett
   * streckblock är bara brus, så cellen döljs tills källan finns. */
  if (v->model_block) {
    if (model_week && model_week->has_pct) {
      lv_obj_remove_flag(v->model_block, LV_OBJ_FLAG_HIDDEN);
      sv_pct_1(model_week->pct, buf, sizeof buf);
      lv_label_set_text(v->model_value, buf);
      bar_set(v->model_fill, STAT_BAR_W, model_week);
    } else {
      lv_obj_add_flag(v->model_block, LV_OBJ_FLAG_HIDDEN);
    }
  }
}

void tokens_apply(const tk_tokens *t) {
  int64_t now = torget_now_us();
  tk.has_data = true;

  char buf[24];

  /* vy 1-2: taken. */
  apply_limits(&tk.claude, &t->claude_session, &t->claude_week,
               &t->claude_model_week);
  apply_limits(&tk.codex, &t->codex_session, &t->codex_week, NULL);
  tk_agent_monitor_set_usage(
      true, tk_agent_usage_pick(&t->claude_session, &t->claude_week,
                                &t->claude_model_week));
  tk_agent_monitor_set_usage(
      false, tk_agent_usage_pick(&t->codex_session, &t->codex_week, NULL));

  /* vy 3: volymen. */
  sv_group_ll(t->day_sessions, buf, sizeof buf);
  lv_label_set_text(tk.sessions_value, buf);
  sv_group_ll((long long)llround(t->month_tokens / 1e6), buf, sizeof buf);
  lv_label_set_text(tk.month_value, buf);

  /* Räknaren backar aldrig: vår extrapolering ligger som mest en hämt-
   * cykel före tjänsten. Ett tapp inom det bruset är extrapoleringen som
   * får komma ikapp; ett större tapp är på riktigt (midnatt nollar). */
  double mtok = t->day_tokens / 1e6;
  double rate = t->day_tokens_per_hour / 1e6;
  double slack = rate / 120.0 + 0.001;
  double shown = sg_ticker_value(&tk.ticker, now);
  double base = (tk.ticker.has_data && mtok > 0.0
                 && mtok < shown && shown - mtok < slack)
                ? shown : mtok;
  sg_ticker_set(&tk.ticker, now, base, rate);

  tk.last_success_us = now;
  /* Brinner det tokens är någon vaken vid tangentbordet — håll skärmen i
   * dagsläge (VibePulse-motsvarigheten till "solen är villkoret"),
   * och låt pulsen andas. Paus ⇒ pricken försvinner helt. */
  if (t->day_tokens_per_hour > 0) {
    torget_keep_awake();
    lv_obj_remove_flag(tk.live_dot, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(tk.live_dot, LV_OBJ_FLAG_HIDDEN);
  }
}

void tokens_apply_agent_status(const tk_agent_snapshot *snapshot) {
  tk_agent_monitor_apply(snapshot, torget_now_us());
}

void tokens_show_view(int idx) {
  if (idx < 0) idx = 0;
  if (idx > TK_VIEWS - 1) idx = TK_VIEWS - 1;
  lv_obj_set_tile_id(tk.tileview, idx, 0, LV_ANIM_OFF);
  for (int i = 0; i < TK_VIEWS; i++)
    lv_obj_set_style_bg_color(tk.dots[i], i == idx ? COL_ACCENT : COL_DOT_OFF, 0);
}

static void set_stale(bool stale) {
  lv_color_t c = stale ? COL_STALE : COL_LABEL;
  for (int i = 0; i < TK_VIEWS; i++)
    if (tk.eyebrows[i]) lv_obj_set_style_text_color(tk.eyebrows[i], c, 0);
}

/* Appens puls: ticka Mtok mellan hämtningarna, tänd stale efter två minuter
 * utan färsk data (Macen kan vara avstängd — det är inte ett fel). */
static void tick_cb(lv_timer_t *t) {
  (void)t;
  int64_t now = torget_now_us();
  tk_agent_monitor_tick(now);

#if defined(ESP_PLATFORM) && defined(TK_AGENT_DEMO)
  static int demo_stage = -1;
  int next_stage = (int)((now / 5000000LL) % 4);
  if (next_stage != demo_stage) {
    demo_stage = next_stage;
    tk_agent_snapshot snapshot = {0};
    snapshot.seq++;
    snprintf(snapshot.claude.task_id, sizeof snapshot.claude.task_id,
             "demo-task");
    snprintf(snapshot.claude.event_id, sizeof snapshot.claude.event_id,
             "demo-%lld-%d", (long long)(now / 20000000LL), demo_stage);
    snprintf(snapshot.claude.project, sizeof snapshot.claude.project,
             "Torget");
    snapshot.claude.state = (tk_agent_state[]){TK_AGENT_WORKING,
                                               TK_AGENT_WAITING,
                                               TK_AGENT_DONE,
                                               TK_AGENT_ERROR}[demo_stage];
    snapshot.claude.activity = demo_stage == 0 ? TK_ACTIVITY_TESTING :
                               demo_stage == 1 ? TK_ACTIVITY_WAITING_APPROVAL :
                                                 TK_ACTIVITY_NONE;
    snapshot.codex.state = TK_AGENT_IDLE;
    tokens_apply_agent_status(&snapshot);
  }
#endif

  if (tk.ticker.has_data)
    set_ticker_mtok(sg_ticker_value(&tk.ticker, now));

  /* NOLLAS OM tickar ner lokalt: snapshotens minuter minus förfluten tid.
   * Ritas bara när minutvärdet faktiskt byter. */
  tk_limit_view *views[2] = { &tk.claude, &tk.codex };
  for (int i = 0; i < 2; i++) {
    tk_limit_view *v = views[i];
    if (!v->hero_snap.has_reset) continue;
    int mins = v->hero_snap.reset_min
             - (int)((now - v->snap_us) / 60000000LL);
    if (mins < 0) mins = 0;
    if (mins != v->shown_min) {
      char buf[24];
      fmt_reset(mins, buf, sizeof buf);
      lv_label_set_text(v->reset_value, buf);
      v->shown_min = mins;
    }
  }

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

  tk.tileview = lv_tileview_create(root);
  lv_obj_set_size(tk.tileview, 480, 480);
  lv_obj_set_style_bg_opa(tk.tileview, LV_OPA_TRANSP, 0);
  lv_obj_set_scrollbar_mode(tk.tileview, LV_SCROLLBAR_MODE_OFF);
  lv_obj_add_event_cb(tk.tileview, tile_changed, LV_EVENT_VALUE_CHANGED, NULL);

  /* --- vy 1-2: taken (Clawdmeter-tanken), en per leverantör -------------- */
  static const char *LIMIT_EYEBROWS[2] = { "CLAUDE JUST NU", "CODEX JUST NU" };
  tk_limit_view *limit_views[2] = { &tk.claude, &tk.codex };
  for (int i = 0; i < 2; i++) {
    lv_obj_t *tile = view(i, LIMIT_EYEBROWS[i], i);
    lv_obj_t *h = hero_row(tile);
    limit_views[i]->pct_value = label(h, &plex_num_146, COL_WHITE);
    lv_label_set_text(limit_views[i]->pct_value, "–");
    lv_obj_t *u = label(h, &plex_text_32, COL_LABEL);
    lv_label_set_text(u, "%");
    lv_obj_set_style_pad_left(u, 11, 0);
    lv_obj_set_style_translate_y(u, -18, 0);

    /* Herons progressbar — samma anatomi som återbetalningsbaren, men
     * färgen följer nivån och fyllnaden rullar animerat. */
    lv_obj_t *track = bare(tile);
    lv_obj_set_size(track, LV_PCT(100), 5);
    lv_obj_set_style_radius(track, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(track, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(track, COL_TRACK, 0);
    lv_obj_set_style_margin_bottom(track, 14, 0);
    limit_views[i]->hero_fill = bare(track);
    lv_obj_set_size(limit_views[i]->hero_fill, 0, 5);
    lv_obj_set_style_radius(limit_views[i]->hero_fill, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(limit_views[i]->hero_fill, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(limit_views[i]->hero_fill, COL_LVL_LOW, 0);

    lv_obj_t *stats = stats_row(tile);
    stat_block(stats, "NOLLAS OM", &limit_views[i]->reset_value, "", NULL);
    limit_views[i]->week_block =
      stat_block(stats, "SESSIONEN", &limit_views[i]->week_value, "%",
                 &limit_views[i]->week_fill);
    /* Claude-vyn får usage-panelens tredje rad: veckofönstret för tyngsta
     * modellen (Fable på Max-planen). Codex saknar motsvarighet. */
    if (i == 0)
      limit_views[i]->model_block =
        stat_block(stats, "FABLE", &limit_views[i]->model_value, "%",
                   &limit_views[i]->model_fill);
  }

  /* --- vy 3: volymen ------------------------------------------------------ */
  lv_obj_t *t2 = view(2, "CLAUDE IDAG", 2);

  /* Hjärtslaget: uppe till höger, utanför flexflödet. Dolt tills första
   * hämtningen visar att tokens faktiskt brinner. */
  tk.live_dot = bare(t2);
  lv_obj_add_flag(tk.live_dot, LV_OBJ_FLAG_IGNORE_LAYOUT);
  lv_obj_set_size(tk.live_dot, 12, 12);
  lv_obj_set_style_radius(tk.live_dot, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(tk.live_dot, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(tk.live_dot, COL_ACCENT, 0);
  lv_obj_align(tk.live_dot, LV_ALIGN_TOP_RIGHT, 0, 5);
  lv_obj_add_flag(tk.live_dot, LV_OBJ_FLAG_HIDDEN);
  breath_start(tk.live_dot);

  /* 118-valör (årsvyns storlek): tresiffriga Mtok-dagar + enheten ryms
   * först då bevisligen på raden. */
  lv_obj_t *h2 = hero_row(t2);
  lv_obj_t *spans = lv_spangroup_create(h2);
  tk.span_group = spans;
  lv_spangroup_set_align(spans, LV_TEXT_ALIGN_LEFT);
  lv_obj_remove_flag(spans, LV_OBJ_FLAG_CLICKABLE);
  tk.sp_whole = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(tk.sp_whole), &plex_num_118);
  lv_style_set_text_color(lv_span_get_style(tk.sp_whole), COL_WHITE);
  tk.sp_frac = lv_spangroup_new_span(spans);
  lv_style_set_text_font(lv_span_get_style(tk.sp_frac), &plex_num_50);
  lv_style_set_text_color(lv_span_get_style(tk.sp_frac), COL_FRAC);

  lv_obj_t *unit2 = label(h2, &plex_text_32, COL_LABEL);
  lv_label_set_text(unit2, "Mtok");
  lv_obj_set_style_pad_left(unit2, 11, 0);
  lv_obj_set_style_translate_y(unit2, -14, 0);

  lv_obj_t *stats2 = stats_row(t2);
  stat_block(stats2, "SESSIONER", &tk.sessions_value, "", NULL);
  stat_block(stats2, "DENNA MÅNAD", &tk.month_value, "Mtok", NULL);

  /* Utan data: streck, aldrig nollor. */
  lv_span_set_text(tk.sp_whole, "–");
  lv_span_set_text(tk.sp_frac, "");
  lv_spangroup_refresh(tk.span_group);

  /* --- prickarna ---------------------------------------------------------- */
  lv_obj_t *dots_box = bare(root);
  lv_obj_align(dots_box, LV_ALIGN_BOTTOM_MID, 0, -15);
  lv_obj_set_flex_flow(dots_box, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(dots_box, 7, 0);
  for (int i = 0; i < TK_VIEWS; i++) {
    tk.dots[i] = bare(dots_box);
    lv_obj_set_size(tk.dots[i], 6, 6);
    lv_obj_set_style_radius(tk.dots[i], LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(tk.dots[i], LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(tk.dots[i], i == 0 ? COL_ACCENT : COL_DOT_OFF, 0);
  }

  /* Agentmonitorn är en helskärms-syskonoverlay ovanpå tileview + prickar.
   * Den börjar dold och rör därför inte den ordinarie VibePulse-vyn. */
  tk_agent_monitor_create(root);

  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);

#ifdef ESP_PLATFORM
  /* Nätverket är appens: hämttasken väntar själv på torget_net_wait().
   * Simulatorn kompilerar inga nätadaptrar — den matar apply med fixtures. */
  tokens_net_start();
  tokens_agent_net_start();
#endif
}

const torget_app_t tokens_app = {
  .api_version = TORGET_APP_API_VERSION,
  .name = "VIBEPULSE",
  .icon = {
    /* V + puls-prick i samma deklarativa 96-pixelsystem som Solelkollen. */
    .font = &plex_icon_64,
    .glyph = "V",
    .plate_hex = 0x181636,
    .glyph_hex = 0xFFFFFF,
    .dot_hex = 0x7770FF,
  },
  .create = tk_create,
  .enter = NULL,
  .leave = NULL,
};
