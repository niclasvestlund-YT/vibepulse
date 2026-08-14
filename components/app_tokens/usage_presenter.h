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

/* One subscription's own answer. Each provider is normalised to its OWN plan
 * cost, so break-even lands at the same fraction for every row -- which is
 * what lets a single shared rule down the page mean "past the line or not"
 * for all of them at once. */
typedef struct {
  usage_provider provider;
  char name[12];
  char money[USAGE_CARD_PCT_CAP];   /* "$280" -- the row's only figure */
  double bar_fraction;
  int has_bar;
} usage_value_row;

typedef struct {
  usage_value_state state;
  /* The argument, stated in full above the hero: "$312 VIA API · $220 PAID".
   * The hero is the verdict; this is the evidence it rests on. Without it a
   * multiple has no referent, and a lone dollar figure on a page titled
   * VALUE is ambiguous between what you spent and what you got.
   *
   * Nothing here is EARNED. The figure is what this month's tokens would
   * have cost at API list rates had you bought them that way; the saving is
   * the gap between it and the subscription. Saying "earned" would claim
   * income that does not exist. */
  char evidence[56];
  char hero_text[USAGE_CARD_PCT_CAP];
  /* 1 when the hero is a WORD and must render in the 48 px headline font. An
   * en dash at hero size is a bare white rectangle -- it reads as a
   * rendering fault rather than as "unknown". */
  int hero_is_word;
  /* 0 below break-even. There is no red in the palette and none is needed:
   * the absence of the money accent, plus bars short of the rule, says it. */
  int hero_ahead;
  /* Where break-even sits on every bar. Identical for all rows by
   * construction, so one rule can be drawn once and labelled once. */
  double break_even_fraction;
  int show_rule;
  /* Only providers that actually spent. One provider means one row, and a
   * different layout -- not a centred stub with the other half missing. */
  int row_count;
  usage_value_row rows[2];
  /* One-provider layout only: the combined pair moves into a stat footer. */
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
