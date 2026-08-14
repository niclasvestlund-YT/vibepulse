#ifndef USAGE_PRESENTER_H
#define USAGE_PRESENTER_H

#include <stdint.h>
#include <stddef.h>

#include "agent_status.h"
#include "tokens.h"

#define USAGE_CARD_LABEL_CAP 24
#define USAGE_CARD_PCT_CAP 16
#define USAGE_CARD_DELTA_CAP 24
#define USAGE_CARD_RESET_CAP 32
#define USAGE_CARD_SHORT_CAP 24

typedef enum {
  USAGE_PROVIDER_CLAUDE,
  USAGE_PROVIDER_CODEX,
} usage_provider;

typedef enum {
  USAGE_CARD_MODEL_WEEK,
  USAGE_CARD_ALL_WEEK,
  USAGE_CARD_FIVE_HOURS,
} usage_card_kind;

typedef struct {
  usage_card_kind kind;
  char label[USAGE_CARD_LABEL_CAP];
  char pct_text[USAGE_CARD_PCT_CAP];
  char delta_text[USAGE_CARD_DELTA_CAP];
  char reset_text[USAGE_CARD_RESET_CAP];
  char reset_short_text[USAGE_CARD_SHORT_CAP];
  double pct;
  double delta_pct;
  int has_pct;
  int has_delta;
  int stale;
} usage_card_view;

typedef struct {
  usage_provider provider;
  char provider_label[12];
  usage_card_view quota;
} usage_hero_view;

typedef enum {
  USAGE_QUOTA_CLAUDE_MODEL,
  USAGE_QUOTA_CLAUDE_ALL,
  USAGE_QUOTA_CODEX_WEEK,
} usage_quota_scope;

typedef struct {
  usage_provider provider;
  usage_card_view quota;
} usage_quota_page_view;

typedef struct {
  int row_count;
  usage_card_view rows[2];
} usage_detail_page_view;

typedef struct {
  usage_provider provider;
  usage_card_view quota;
} usage_overview_row_view;

typedef struct {
  int row_count;
  usage_overview_row_view rows[2];
} usage_overview_page_view;

#define USAGE_FORECAST_HEADLINE_CAP 48
#define USAGE_FORECAST_DETAIL_CAP 40

typedef struct {
  usage_provider provider;
  char label[USAGE_CARD_LABEL_CAP];
  char pct_text[USAGE_CARD_PCT_CAP];
  char headline[USAGE_FORECAST_HEADLINE_CAP];
  char detail[USAGE_FORECAST_DETAIL_CAP];
  double pct;
  int has_pct;
  int visible;
} usage_forecast_row_view;

typedef struct {
  int row_count;
  usage_forecast_row_view rows[2];
} usage_forecast_page_view;

#define USAGE_VALUE_TEXT_CAP 40

/* Full-scale value of the break-even bar, in multiples. 2x puts break-even
 * dead centre and still leaves the top half meaningful before it clamps. */
#define USAGE_VALUE_BAR_SCALE 2.0

/* Mirrors tk_value_state, but the presenter owns what the page may render:
 * only OK earns a multiple, only OK and NO_PLAN_COST earn dollars. */
typedef enum {
  USAGE_VALUE_UNAVAILABLE,
  USAGE_VALUE_PARTIAL,
  USAGE_VALUE_NO_PLAN_COST,
  USAGE_VALUE_OK,
} usage_value_state;

/* One subscription's contribution. Only providers whose OWN cost is declared
 * reach the ratio; the rest are reported but never divided against someone
 * else's subscription. */
typedef struct {
  usage_provider provider;
  char name[12];
  char money[USAGE_CARD_PCT_CAP];
  double share;      /* of the drawn fill, 0..1 */
  int counted;       /* in the ratio, i.e. its plan cost is known */
} usage_value_row;

typedef struct {
  usage_value_state state;
  /* The verdict, in words, because the page's whole question is which of the
   * two costs came out larger. The bar's marker says it visually; this says
   * it for anyone who does not read the bar. */
  char verdict[40];
  char hero_text[USAGE_CARD_PCT_CAP];
  /* 1 when the hero is a WORD and must render in the 48 px headline font. An
   * en dash at hero size is a bare white rectangle -- a rendering fault, not
   * a value. */
  int hero_is_word;
  /* "CLAUDE $280 · CODEX $32" -- the split, one quiet line. */
  char attribution[80];
  /* Fill on a fixed 0..2x scale so break-even is always the halfway mark and
   * one marker can be drawn once and labelled once. */
  double bar_fraction;
  double break_even_fraction;
  int show_bar;
  int row_count;
  usage_value_row rows[2];
  char api_cost[USAGE_CARD_PCT_CAP];
  char paid[USAGE_CARD_PCT_CAP];
} usage_value_page_view;

void usage_presenter_build_value(const tk_tokens *tokens,
                                 usage_value_page_view *out);
void usage_presenter_build_hero(const tk_tokens *tokens,
                                usage_provider provider,
                                usage_hero_view *out);
void usage_presenter_build_quota_page(const tk_tokens *tokens,
                                      usage_quota_scope scope,
                                      usage_quota_page_view *out);
void usage_presenter_build_claude_details(
    const tk_tokens *tokens, usage_detail_page_view *out);
void usage_presenter_build_overview(
    const tk_tokens *tokens, usage_overview_page_view *out);
void usage_presenter_build_forecasts(const tk_tokens *tokens,
                                     usage_forecast_page_view *out);
void usage_presenter_format_agent_metadata(const tk_agent_status *agent,
                                           char *out, size_t capacity);
const char *usage_presenter_quota_status_text(int has_data, int stale,
                                              const char *live_context);

#endif
