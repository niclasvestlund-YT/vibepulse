#ifndef USAGE_PRESENTER_H
#define USAGE_PRESENTER_H

#include <stdint.h>

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
} usage_card_view;

typedef struct {
  usage_provider provider;
  int card_count;
  usage_card_view cards[2];
  tk_forecast forecast;
} usage_provider_view;

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
} usage_forecast_row_view;

typedef struct {
  int row_count;
  usage_forecast_row_view rows[2];
} usage_forecast_page_view;

void usage_presenter_build_provider(const tk_tokens *tokens,
                                    usage_provider provider,
                                    uint32_t elapsed_ms,
                                    usage_provider_view *out);
void usage_presenter_build_forecasts(const tk_tokens *tokens,
                                     usage_forecast_page_view *out);

#endif
