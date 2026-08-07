#include "usage_presenter.h"

#include <stdio.h>
#include <string.h>

static void format_reset(const tk_limit *limit, char *out, size_t capacity) {
  if (!limit->has_reset) return;
  if (limit->reset_min >= 60) {
    snprintf(out, capacity, "NOLLAS OM %d H %02d",
             limit->reset_min / 60, limit->reset_min % 60);
  } else {
    snprintf(out, capacity, "NOLLAS OM %d MIN", limit->reset_min);
  }
}

static void build_card(usage_card_view *out, usage_card_kind kind,
                       const char *label, const tk_limit *limit) {
  memset(out, 0, sizeof *out);
  out->kind = kind;
  snprintf(out->label, sizeof out->label, "%s", label);
  if (limit->has_pct) {
    out->has_pct = 1;
    out->pct = limit->pct;
    snprintf(out->pct_text, sizeof out->pct_text, "%.0f%%", limit->pct);
  } else {
    snprintf(out->pct_text, sizeof out->pct_text, "–");
  }
  if (limit->has_delta && limit->has_pct) {
    out->has_delta = 1;
    out->delta_pct = limit->delta_pct;
    snprintf(out->delta_text, sizeof out->delta_text,
             kind == USAGE_CARD_FIVE_HOURS ? "+%.0f%% SENASTE H" :
                                             "+%.0f%% IDAG",
             limit->delta_pct);
  }
  format_reset(limit, out->reset_text, sizeof out->reset_text);
}

static void build_claude(const tk_tokens *tokens, uint32_t elapsed_ms,
                         usage_provider_view *out) {
  const int named_model = tokens->has_claude_model_week_label &&
                          tokens->claude_model_week_label[0] != '\0' &&
                          tokens->claude_model_week.has_pct;
  if (named_model) {
    build_card(&out->cards[0], USAGE_CARD_MODEL_WEEK,
               tokens->claude_model_week_label,
               &tokens->claude_model_week);
    out->card_count = 1;
    const int has_week = tokens->claude_week.has_pct;
    const int has_session = tokens->claude_session.has_pct;
    if (has_week || has_session) {
      const int show_session = has_session &&
          (!has_week || ((elapsed_ms / 7000U) % 2U) == 1U);
      build_card(&out->cards[1],
                 show_session ? USAGE_CARD_FIVE_HOURS :
                                USAGE_CARD_ALL_WEEK,
                 show_session ? "5 TIMMAR" : "ALLA · VECKA",
                 show_session ? &tokens->claude_session :
                                &tokens->claude_week);
      out->card_count = 2;
    }
    return;
  }

  const int show_week = tokens->claude_week.has_pct ||
                        !tokens->claude_session.has_pct;
  build_card(&out->cards[0],
             show_week ? USAGE_CARD_ALL_WEEK : USAGE_CARD_FIVE_HOURS,
             show_week ? "ALLA · VECKA" : "5 TIMMAR",
             show_week ? &tokens->claude_week : &tokens->claude_session);
  out->card_count = 1;
  if (show_week && tokens->claude_session.has_pct) {
    build_card(&out->cards[1], USAGE_CARD_FIVE_HOURS, "5 TIMMAR",
               &tokens->claude_session);
    out->card_count = 2;
  }
}

void usage_presenter_build_provider(const tk_tokens *tokens,
                                    usage_provider provider,
                                    uint32_t elapsed_ms,
                                    usage_provider_view *out) {
  if (!tokens || !out) return;
  memset(out, 0, sizeof *out);
  out->provider = provider;
  if (provider == USAGE_PROVIDER_CLAUDE) {
    out->forecast = tokens->claude_forecast;
    build_claude(tokens, elapsed_ms, out);
    return;
  }
  out->forecast = tokens->codex_forecast;
  build_card(&out->cards[0], USAGE_CARD_ALL_WEEK, "ALLA · VECKA",
             &tokens->codex_week);
  out->card_count = 1;
}
