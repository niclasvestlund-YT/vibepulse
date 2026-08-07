#include "usage_presenter.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

static void format_reset(const tk_limit *limit, char *out, size_t capacity) {
  if (!limit->has_reset) return;
  if (limit->reset_min >= 24 * 60) {
    snprintf(out, capacity, "NOLLAS OM %d D %d H",
             limit->reset_min / (24 * 60),
             (limit->reset_min / 60) % 24);
  } else if (limit->reset_min >= 60) {
    snprintf(out, capacity, "NOLLAS OM %d H %02d",
             limit->reset_min / 60, limit->reset_min % 60);
  } else {
    snprintf(out, capacity, "NOLLAS OM %d MIN", limit->reset_min);
  }
}

static void format_reset_short(const tk_limit *limit, char *out,
                               size_t capacity) {
  if (!limit->has_reset) return;
  if (limit->reset_min >= 24 * 60) {
    snprintf(out, capacity, "%d D %d H KVAR", limit->reset_min / (24 * 60),
             (limit->reset_min / 60) % 24);
  } else if (limit->reset_min >= 60) {
    snprintf(out, capacity, "%d H %02d MIN KVAR", limit->reset_min / 60,
             limit->reset_min % 60);
  } else {
    snprintf(out, capacity, "%d MIN KVAR", limit->reset_min);
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
    snprintf(out->reset_text, sizeof out->reset_text, "QUOTA SAKNAS");
  }
  if (limit->has_delta && limit->has_pct) {
    out->has_delta = 1;
    out->delta_pct = limit->delta_pct;
    snprintf(out->delta_text, sizeof out->delta_text,
             kind == USAGE_CARD_FIVE_HOURS ? "+%.0f%% SENASTE H" :
                                             "+%.0f%% IDAG",
             limit->delta_pct);
  }
  if (limit->has_pct)
    format_reset(limit, out->reset_text, sizeof out->reset_text);
  format_reset_short(limit, out->reset_short_text,
                     sizeof out->reset_short_text);
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

static void format_pace(double factor, char *out, size_t capacity) {
  int tenths = (int)(factor * 10.0 + 0.5);
  snprintf(out, capacity, "ÖKA %d,%d× FÖR ATT MAXA",
           tenths / 10, tenths % 10);
}

static int format_exhaustion(const tk_forecast *forecast, char *out,
                             size_t capacity) {
  if (!forecast->has_at_epoch) return 0;
  time_t timestamp = (time_t)forecast->at_epoch;
  struct tm *local = localtime(&timestamp);
  if (!local || local->tm_wday < 0 || local->tm_wday > 6) return 0;
  static const char *weekdays[] = {
      "SÖN", "MÅN", "TIS", "ONS", "TOR", "FRE", "LÖR",
  };
  snprintf(out, capacity, "QUOTAN TAR SLUT %s %02d:%02d",
           weekdays[local->tm_wday], local->tm_hour, local->tm_min);
  return 1;
}

static void format_offset(const tk_forecast *forecast, char *out,
                          size_t capacity) {
  if (!forecast->has_offset_min) return;
  int64_t minutes = forecast->offset_min;
  int64_t magnitude = minutes < 0 ? -minutes : minutes;
  long long hours = (long long)((magnitude + 30) / 60);
  if (minutes < 0) {
    snprintf(out, capacity, "%lld H TIDIGT", hours);
  } else if (minutes > 0) {
    snprintf(out, capacity, "%lld H SENT", hours);
  } else {
    snprintf(out, capacity, "VID RESET");
  }
}

static void build_forecast_row(usage_forecast_row_view *out,
                               usage_provider provider, const char *label,
                               const tk_limit *week,
                               const tk_forecast *forecast) {
  memset(out, 0, sizeof *out);
  out->provider = provider;
  snprintf(out->label, sizeof out->label, "%s", label);
  if (week->has_pct) {
    out->has_pct = 1;
    out->pct = week->pct;
    snprintf(out->pct_text, sizeof out->pct_text, "%.0f%%", week->pct);
  } else {
    snprintf(out->pct_text, sizeof out->pct_text, "–");
    snprintf(out->headline, sizeof out->headline, "PROGNOS SAKNAS");
    return;
  }

  switch (forecast->state) {
    case TK_FORECAST_COLLECTING:
      snprintf(out->headline, sizeof out->headline, "SAMLAR TAKT");
      break;
    case TK_FORECAST_AT_RESET:
      if (!forecast->has_pct_at_reset || !forecast->has_pace_factor) {
        snprintf(out->headline, sizeof out->headline, "PROGNOS SAKNAS");
        break;
      }
      snprintf(out->headline, sizeof out->headline, "%d%% VID RESET",
               forecast->pct_at_reset);
      format_pace(forecast->pace_factor, out->detail, sizeof out->detail);
      break;
    case TK_FORECAST_EXHAUSTS:
      if (!format_exhaustion(forecast, out->headline,
                             sizeof out->headline)) {
        snprintf(out->headline, sizeof out->headline, "PROGNOS SAKNAS");
        break;
      }
      format_offset(forecast, out->detail, sizeof out->detail);
      break;
    case TK_FORECAST_UNAVAILABLE:
    default:
      snprintf(out->headline, sizeof out->headline, "PROGNOS SAKNAS");
      break;
  }
}

void usage_presenter_build_forecasts(const tk_tokens *tokens,
                                     usage_forecast_page_view *out) {
  if (!tokens || !out) return;
  memset(out, 0, sizeof *out);
  build_forecast_row(&out->rows[0], USAGE_PROVIDER_CLAUDE,
                     "CLAUDE · VECKA", &tokens->claude_week,
                     &tokens->claude_forecast);
  build_forecast_row(&out->rows[1], USAGE_PROVIDER_CODEX,
                     "CODEX · VECKA", &tokens->codex_week,
                     &tokens->codex_forecast);
  out->row_count = 2;
}
