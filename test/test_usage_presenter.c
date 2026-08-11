#include <stdio.h>
#include <string.h>
#include <time.h>

#include "../components/app_tokens/usage_presenter.h"

static int failures = 0;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tk_limit limit(double pct, int reset_min, double delta) {
  tk_limit out = {0};
  out.pct = pct;
  out.reset_min = reset_min;
  out.delta_pct = delta;
  out.has_pct = 1;
  out.has_reset = 1;
  out.has_delta = 1;
  return out;
}

int main(void) {
  tk_tokens tokens = {0};
  tokens.claude_model_week = limit(73, 3120, 3);
  tokens.claude_week = limit(47, 300, 7);
  tokens.claude_session = limit(21, 80, 11);
  tokens.has_claude_model_week_label = 1;
  snprintf(tokens.claude_model_week_label,
           sizeof tokens.claude_model_week_label, "FABLE · WEEK");

  usage_hero_view hero = {0};
  usage_presenter_build_hero(&tokens, USAGE_PROVIDER_CLAUDE, &hero);
  check("Claude hero uses real model week",
        strcmp(hero.quota.label, "FABLE · WEEK") == 0 &&
        strcmp(hero.quota.pct_text, "73%") == 0);
  check("hero reset is English",
        strcmp(hero.quota.reset_text, "RESET IN 2D 4H") == 0);
  check("week delta includes the percentage unit",
        strcmp(hero.quota.delta_text, "+3% TODAY") == 0);

  tk_agent_status metadata = {0};
  metadata.has_model = true;
  metadata.has_effort = true;
  snprintf(metadata.model, sizeof metadata.model, "OPUS 5");
  snprintf(metadata.effort, sizeof metadata.effort, "ULTRA");
  char metadata_text[48];
  usage_presenter_format_agent_metadata(&metadata, metadata_text,
                                        sizeof metadata_text);
  check("model and effort share compact hero metadata",
        strcmp(metadata_text, "OPUS 5 · ULTRA") == 0);
  metadata.has_effort = false;
  usage_presenter_format_agent_metadata(&metadata, metadata_text,
                                        sizeof metadata_text);
  check("model metadata remains useful without effort",
        strcmp(metadata_text, "OPUS 5") == 0);
  usage_presenter_format_agent_metadata(NULL, metadata_text,
                                        sizeof metadata_text);
  check("missing agent metadata stays blank", metadata_text[0] == '\0');

  check("fresh quota is live",
        strcmp(usage_presenter_data_status_text(true, false), "LIVE") == 0);
  check("retained quota is stale",
        strcmp(usage_presenter_data_status_text(true, true), "STALE") == 0);
  check("missing quota wins over stale transport",
        strcmp(usage_presenter_data_status_text(false, true), "NO DATA") == 0);

  usage_detail_page_view details = {0};
  usage_presenter_build_claude_details(&tokens, &details);
  check("details are stable and ordered",
        details.row_count == 2 &&
        strcmp(details.rows[0].label, "WEEKLY · ALL MODELS") == 0 &&
        strcmp(details.rows[1].label, "5-HOUR LIMIT") == 0);
  check("hour delta is English",
        strcmp(details.rows[1].delta_text, "+11 LAST HOUR") == 0);

  usage_overview_page_view overview = {0};
  usage_presenter_build_overview(&tokens, &overview);
  check("overview contains both providers",
        overview.row_count == 2 &&
        overview.rows[0].provider == USAGE_PROVIDER_CLAUDE &&
        overview.rows[1].provider == USAGE_PROVIDER_CODEX);
  check("overview reuses Claude hero quota",
        strcmp(overview.rows[0].quota.label, "FABLE · WEEK") == 0);

  tk_tokens codex = {0};
  codex.codex_week = limit(57, 2210, 5);
  usage_presenter_build_hero(&codex, USAGE_PROVIDER_CODEX, &hero);
  check("Codex stays used, never double inverted",
        strcmp(hero.quota.label, "WEEKLY") == 0 &&
        strcmp(hero.quota.pct_text, "57%") == 0);

  tk_tokens missing = {0};
  usage_presenter_build_hero(&missing, USAGE_PROVIDER_CODEX, &hero);
  check("missing limits remain unavailable",
        strcmp(hero.quota.pct_text, "–") == 0 &&
        strcmp(hero.quota.reset_text, "USAGE UNAVAILABLE") == 0 &&
        hero.quota.delta_text[0] == '\0');

  tk_tokens forecasts = {0};
  forecasts.claude_week = limit(47, 300, 7);
  forecasts.claude_forecast.state = TK_FORECAST_AT_RESET;
  forecasts.claude_forecast.has_pct_at_reset = 1;
  forecasts.claude_forecast.pct_at_reset = 85;
  forecasts.claude_forecast.has_pace_factor = 1;
  forecasts.claude_forecast.pace_factor = 1.4;
  usage_forecast_page_view forecast_page = {0};
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("forecasts contain both providers", forecast_page.row_count == 2);
  check("forecast label is stable English copy",
        strcmp(forecast_page.rows[0].label, "CLAUDE · WEEKLY") == 0);
  check("at-reset forecast keeps the weekly percentage",
        strcmp(forecast_page.rows[0].pct_text, "47%") == 0);
  check("at-reset forecast has an English headline",
        strcmp(forecast_page.rows[0].headline, "85% AT RESET") == 0);
  check("at-reset forecast has an English pace detail",
        strcmp(forecast_page.rows[0].detail,
               "INCREASE 1.4x TO MAX OUT") == 0);

  tk_tokens exhausts = {0};
  exhausts.codex_week = limit(35, 2210, 5);
  exhausts.codex_forecast.state = TK_FORECAST_EXHAUSTS;
  exhausts.codex_forecast.has_at_epoch = 1;
  struct tm saturday = {0};
  saturday.tm_year = 126;
  saturday.tm_mon = 7;
  saturday.tm_mday = 8;
  saturday.tm_hour = 5;
  saturday.tm_isdst = -1;
  exhausts.codex_forecast.at_epoch = (int64_t)mktime(&saturday);
  exhausts.codex_forecast.has_offset_min = 1;
  exhausts.codex_forecast.offset_min = -540;
  usage_presenter_build_forecasts(&exhausts, &forecast_page);
  check("exhaustion forecast keeps the weekly percentage",
        strcmp(forecast_page.rows[1].pct_text, "35%") == 0);
  check("exhaustion forecast has local weekday and time",
        strcmp(forecast_page.rows[1].headline,
               "QUOTA RUNS OUT SAT 05:00") == 0);
  check("exhaustion forecast formats negative offset separately",
        strcmp(forecast_page.rows[1].detail, "9H EARLY") == 0);

  tk_tokens collecting = {0};
  collecting.claude_week = limit(5, 9841, 1);
  collecting.claude_forecast.state = TK_FORECAST_COLLECTING;
  usage_presenter_build_forecasts(&collecting, &forecast_page);
  check("collecting forecast keeps the weekly percentage",
        strcmp(forecast_page.rows[0].pct_text, "5%") == 0);
  check("collecting forecast has an English headline",
        strcmp(forecast_page.rows[0].headline, "COLLECTING PACE") == 0);
  check("collecting forecast has no detail",
        forecast_page.rows[0].detail[0] == '\0');

  tk_tokens unavailable = {0};
  unavailable.codex_week = limit(35, 2210, 5);
  unavailable.codex_forecast.state = TK_FORECAST_UNAVAILABLE;
  usage_presenter_build_forecasts(&unavailable, &forecast_page);
  check("unavailable forecast keeps the weekly percentage",
        strcmp(forecast_page.rows[1].pct_text, "35%") == 0);
  check("unavailable forecast has an English headline",
        strcmp(forecast_page.rows[1].headline,
               "FORECAST UNAVAILABLE") == 0);
  check("unavailable forecast has no detail",
        forecast_page.rows[1].detail[0] == '\0');

  if (failures == 0) {
    printf("OK: all usage presenter tests pass\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
