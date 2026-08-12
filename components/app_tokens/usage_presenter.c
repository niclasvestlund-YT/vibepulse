#include "usage_presenter.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

void usage_presenter_format_agent_metadata(const tk_agent_status *agent,
                                           char *out, size_t capacity) {
  if (!out || capacity == 0) return;
  out[0] = '\0';
  if (!agent) return;
  if (agent->has_model && agent->has_effort) {
    snprintf(out, capacity, "%s · %s", agent->model, agent->effort);
  } else if (agent->has_model) {
    snprintf(out, capacity, "%s", agent->model);
  } else if (agent->has_effort) {
    snprintf(out, capacity, "%s", agent->effort);
  }
}

const char *usage_presenter_quota_status_text(int has_data, int stale,
                                              const char *live_context) {
  if (!has_data) return "NO DATA";
  if (stale) return "STALE";
  if (live_context && live_context[0]) return live_context;
  return "LIVE";
}

static void format_reset(const tk_limit *limit,
                         char *long_out, size_t long_capacity,
                         char *short_out, size_t short_capacity) {
  if (!limit->has_reset) return;
  if (limit->reset_min >= 24 * 60) {
    snprintf(long_out, long_capacity, "RESET IN %dD %dH",
             limit->reset_min / (24 * 60),
             (limit->reset_min / 60) % 24);
    snprintf(short_out, short_capacity, "%dD %dH",
             limit->reset_min / (24 * 60),
             (limit->reset_min / 60) % 24);
  } else if (limit->reset_min >= 60) {
    snprintf(long_out, long_capacity, "RESET IN %dH %02dM",
             limit->reset_min / 60, limit->reset_min % 60);
    snprintf(short_out, short_capacity, "%dH %02dM",
             limit->reset_min / 60, limit->reset_min % 60);
  } else {
    snprintf(long_out, long_capacity, "RESET IN %dM", limit->reset_min);
    snprintf(short_out, short_capacity, "%dM", limit->reset_min);
  }
}

static void build_card(usage_card_view *out, usage_card_kind kind,
                       const char *label, const tk_limit *limit) {
  memset(out, 0, sizeof *out);
  out->kind = kind;
  out->stale = limit->stale;
  snprintf(out->label, sizeof out->label, "%s", label);
  if (limit->has_pct) {
    out->has_pct = 1;
    out->pct = limit->pct;
    snprintf(out->pct_text, sizeof out->pct_text, "%.0f%%", limit->pct);
  } else {
    snprintf(out->pct_text, sizeof out->pct_text, "–");
    snprintf(out->delta_text, sizeof out->delta_text, "–");
    snprintf(out->reset_short_text, sizeof out->reset_short_text, "–");
    snprintf(out->reset_text, sizeof out->reset_text, "USAGE UNAVAILABLE");
  }
  if (limit->has_delta && limit->has_pct) {
    out->has_delta = 1;
    out->delta_pct = limit->delta_pct;
    snprintf(out->delta_text, sizeof out->delta_text, "+%.0f%%",
             limit->delta_pct);
  }
  if (limit->has_pct) {
    format_reset(limit, out->reset_text, sizeof out->reset_text,
                 out->reset_short_text, sizeof out->reset_short_text);
    if (!limit->has_reset)
      snprintf(out->reset_short_text, sizeof out->reset_short_text, "–");
  }
}

static void build_hero_quota(const tk_tokens *tokens, usage_provider provider,
                             usage_card_view *out) {
  if (provider == USAGE_PROVIDER_CODEX) {
    build_card(out, USAGE_CARD_ALL_WEEK, "WEEKLY", &tokens->codex_week);
    return;
  }
  if (tokens->has_claude_model_week_label &&
      tokens->claude_model_week_label[0] &&
      tokens->claude_model_week.has_pct) {
    build_card(out, USAGE_CARD_MODEL_WEEK, tokens->claude_model_week_label,
               &tokens->claude_model_week);
    return;
  }
  build_card(out, USAGE_CARD_ALL_WEEK,
             tokens->claude_week.has_pct ? "WEEKLY · ALL MODELS"
                                         : "WEEKLY",
             &tokens->claude_week);
}

void usage_presenter_build_hero(const tk_tokens *tokens,
                                usage_provider provider,
                                usage_hero_view *out) {
  tk_tokens empty = {0};
  if (!out) return;
  if (!tokens) tokens = &empty;
  memset(out, 0, sizeof *out);
  out->provider = provider;
  snprintf(out->provider_label, sizeof out->provider_label, "%s",
           provider == USAGE_PROVIDER_CODEX ? "CODEX" : "CLAUDE");
  build_hero_quota(tokens, provider, &out->quota);
}

void usage_presenter_build_quota_page(const tk_tokens *tokens,
                                      usage_quota_scope scope,
                                      usage_quota_page_view *out) {
  tk_tokens empty = {0};
  if (!out) return;
  if (!tokens) tokens = &empty;
  memset(out, 0, sizeof *out);

  switch (scope) {
    case USAGE_QUOTA_CLAUDE_MODEL:
      out->provider = USAGE_PROVIDER_CLAUDE;
      build_card(&out->quota, USAGE_CARD_MODEL_WEEK,
                 tokens->has_claude_model_week_label &&
                         tokens->claude_model_week_label[0]
                     ? tokens->claude_model_week_label
                     : "FABLE · WEEK",
                 &tokens->claude_model_week);
      break;
    case USAGE_QUOTA_CLAUDE_ALL:
      out->provider = USAGE_PROVIDER_CLAUDE;
      build_card(&out->quota, USAGE_CARD_ALL_WEEK,
                 "WEEKLY · ALL MODELS", &tokens->claude_week);
      break;
    case USAGE_QUOTA_CODEX_WEEK:
    default:
      out->provider = USAGE_PROVIDER_CODEX;
      build_card(&out->quota, USAGE_CARD_ALL_WEEK,
                 "WEEKLY", &tokens->codex_week);
      break;
  }
}

void usage_presenter_build_claude_details(
    const tk_tokens *tokens, usage_detail_page_view *out) {
  tk_tokens empty = {0};
  if (!out) return;
  if (!tokens) tokens = &empty;
  memset(out, 0, sizeof *out);
  build_card(&out->rows[0], USAGE_CARD_MODEL_WEEK,
             tokens->has_claude_model_week_label &&
                     tokens->claude_model_week_label[0]
                 ? tokens->claude_model_week_label
                 : "FABLE · WEEK",
             &tokens->claude_model_week);
  build_card(&out->rows[1], USAGE_CARD_ALL_WEEK, "ALL MODELS",
             &tokens->claude_week);
  out->row_count = 2;
}

void usage_presenter_build_overview(
    const tk_tokens *tokens, usage_overview_page_view *out) {
  usage_hero_view hero = {0};
  if (!out) return;
  memset(out, 0, sizeof *out);
  usage_presenter_build_hero(tokens, USAGE_PROVIDER_CLAUDE, &hero);
  out->rows[0].provider = hero.provider;
  out->rows[0].quota = hero.quota;
  usage_presenter_build_hero(tokens, USAGE_PROVIDER_CODEX, &hero);
  out->rows[1].provider = hero.provider;
  out->rows[1].quota = hero.quota;
  out->row_count = 2;
}

static void unavailable_forecast(usage_forecast_row_view *out) {
  snprintf(out->headline, sizeof out->headline, "UNAVAILABLE");
  snprintf(out->detail, sizeof out->detail, "NO RELIABLE FORECAST");
}

static int format_exhaustion_time(const tk_forecast *forecast, char *out,
                                  size_t capacity) {
  if (!forecast->has_at_epoch) return 0;
  time_t timestamp = (time_t)forecast->at_epoch;
  struct tm *local = localtime(&timestamp);
  if (!local || local->tm_wday < 0 || local->tm_wday > 6) return 0;
  static const char *weekdays[] = {
      "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT",
  };
  snprintf(out, capacity, "RUNS OUT %s %02d:%02d",
           weekdays[local->tm_wday], local->tm_hour, local->tm_min);
  return 1;
}

static void format_early(int64_t magnitude, char *out, size_t capacity) {
  if (magnitude < 60) {
    snprintf(out, capacity, "%lldM EARLY", (long long)magnitude);
  } else if (magnitude < 24 * 60) {
    snprintf(out, capacity, "%lldH EARLY",
             (long long)(magnitude / 60));
  } else {
    snprintf(out, capacity, "%lldD %lldH EARLY",
             (long long)(magnitude / (24 * 60)),
             (long long)((magnitude / 60) % 24));
  }
}

static void build_forecast_row(usage_forecast_row_view *out,
                               usage_provider provider, const char *label,
                               const tk_limit *week,
                               const tk_forecast *forecast) {
  memset(out, 0, sizeof *out);
  out->provider = provider;
  snprintf(out->label, sizeof out->label, "%s", label);
  out->visible = week->has_pct;
  if (!out->visible) return;

  switch (forecast->state) {
    case TK_FORECAST_COLLECTING:
      snprintf(out->headline, sizeof out->headline, "LEARNING PACE");
      snprintf(out->detail, sizeof out->detail, "FORECAST NOT READY");
      break;
    case TK_FORECAST_AT_RESET:
      if (!forecast->has_pct_at_reset || !forecast->has_pace_factor) {
        unavailable_forecast(out);
        break;
      }
      {
        int tenths = (int)(forecast->pace_factor * 10.0 + 0.5);
        if (tenths > 10) {
          snprintf(out->headline, sizeof out->headline, "SPEED UP");
          snprintf(out->detail, sizeof out->detail,
                   "%d.%d× CURRENT PACE TO MAX OUT",
                   tenths / 10, tenths % 10);
        } else if (tenths == 10) {
          snprintf(out->headline, sizeof out->headline, "ON PACE");
          snprintf(out->detail, sizeof out->detail,
                   "≈ CURRENT PACE TO MAX OUT");
        } else {
          unavailable_forecast(out);
        }
      }
      break;
    case TK_FORECAST_EXHAUSTS:
      if (!forecast->has_offset_min || forecast->offset_min > 0) {
        unavailable_forecast(out);
        break;
      }
      if (forecast->offset_min == 0) {
        snprintf(out->headline, sizeof out->headline, "ON PACE");
        snprintf(out->detail, sizeof out->detail, "RUNS OUT AT RESET");
        break;
      }
      if (!format_exhaustion_time(forecast, out->detail,
                                  sizeof out->detail)) {
        unavailable_forecast(out);
        break;
      }
      format_early(-forecast->offset_min, out->headline,
                   sizeof out->headline);
      break;
    case TK_FORECAST_UNAVAILABLE:
    default:
      unavailable_forecast(out);
      break;
  }
}

void usage_presenter_build_forecasts(const tk_tokens *tokens,
                                     usage_forecast_page_view *out) {
  if (!tokens || !out) return;
  memset(out, 0, sizeof *out);
  build_forecast_row(&out->rows[0], USAGE_PROVIDER_CLAUDE,
                     "CLAUDE · ALL MODELS", &tokens->claude_week,
                     &tokens->claude_forecast);
  build_forecast_row(&out->rows[1], USAGE_PROVIDER_CODEX,
                     "CODEX · WEEKLY", &tokens->codex_week,
                     &tokens->codex_forecast);
  out->row_count = 2;
}
