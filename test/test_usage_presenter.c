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

static int64_t local_epoch(int year, int month, int day, int hour, int minute) {
  struct tm value = {0};
  value.tm_year = year - 1900;
  value.tm_mon = month - 1;
  value.tm_mday = day;
  value.tm_hour = hour;
  value.tm_min = minute;
  value.tm_isdst = -1;
  return (int64_t)mktime(&value);
}

int main(void) {
  tk_tokens tokens = {0};
  tokens.claude_model_week = limit(73, 3120, 12);
  tokens.claude_week = limit(47, 249, 4);
  tokens.codex_week = limit(57, 2210, 5);
  tokens.claude_session = limit(21, 80, 11);
  tokens.claude_model_week.stale = 1;
  tokens.has_claude_model_week_label = 1;
  snprintf(tokens.claude_model_week_label,
           sizeof tokens.claude_model_week_label, "FABLE · WEEK");

  usage_quota_page_view page = {0};
  usage_presenter_build_quota_page(&tokens, USAGE_QUOTA_CLAUDE_MODEL,
                                   &page);
  check("Fable page uses model-week data",
        page.provider == USAGE_PROVIDER_CLAUDE &&
        strcmp(page.quota.label, "FABLE · WEEK") == 0 &&
        strcmp(page.quota.pct_text, "73%") == 0 &&
        strcmp(page.quota.delta_text, "+12%") == 0 &&
        strcmp(page.quota.reset_short_text, "2D 4H") == 0 &&
        page.quota.stale == 1);
  check("cached Fable keeps trusted label",
        strcmp(page.quota.label, "FABLE · WEEK") == 0);

  usage_presenter_build_quota_page(&tokens, USAGE_QUOTA_CLAUDE_ALL, &page);
  check("Claude all-model page is independent",
        page.provider == USAGE_PROVIDER_CLAUDE &&
        strcmp(page.quota.label, "WEEKLY · ALL MODELS") == 0 &&
        strcmp(page.quota.pct_text, "47%") == 0 &&
        strcmp(page.quota.delta_text, "+4%") == 0 &&
        strcmp(page.quota.reset_short_text, "4H 09M") == 0);

  usage_presenter_build_quota_page(&tokens, USAGE_QUOTA_CODEX_WEEK, &page);
  check("Codex page stays consumed usage",
        page.provider == USAGE_PROVIDER_CODEX &&
        strcmp(page.quota.label, "WEEKLY") == 0 &&
        strcmp(page.quota.pct_text, "57%") == 0 &&
        strcmp(page.quota.reset_short_text, "1D 12H") == 0);

  tk_tokens missing = {0};
  usage_presenter_build_quota_page(&missing, USAGE_QUOTA_CLAUDE_MODEL,
                                   &page);
  check("missing model quota remains truthful",
        strcmp(page.quota.label, "FABLE · WEEK") == 0 &&
        strcmp(page.quota.pct_text, "–") == 0 &&
        strcmp(page.quota.delta_text, "–") == 0 &&
        strcmp(page.quota.reset_short_text, "–") == 0 &&
        !page.quota.has_pct && !page.quota.has_delta);

  usage_detail_page_view details = {0};
  usage_presenter_build_claude_details(&missing, &details);
  check("Claude details use stable Fable identity without data",
        details.row_count == 2 &&
        strcmp(details.rows[0].label, "FABLE · WEEK") == 0 &&
        strcmp(details.rows[0].pct_text, "–") == 0);

  tk_agent_status metadata = {0};
  metadata.has_model = true;
  metadata.has_effort = true;
  snprintf(metadata.model, sizeof metadata.model, "OPUS 5");
  snprintf(metadata.effort, sizeof metadata.effort, "ULTRA");
  char metadata_text[48];
  usage_presenter_format_agent_metadata(&metadata, metadata_text,
                                        sizeof metadata_text);
  check("legacy compact metadata remains available to overlays",
        strcmp(metadata_text, "OPUS 5 · ULTRA") == 0);
  check("fresh quota without agent context is live",
        strcmp(usage_presenter_quota_status_text(true, false, ""),
               "LIVE") == 0);
  check("live agent context replaces live",
        strcmp(usage_presenter_quota_status_text(
                   true, false, "WORKING · 2 CHATS"),
               "WORKING · 2 CHATS") == 0);
  check("retained quota is stale despite agent context",
        strcmp(usage_presenter_quota_status_text(
                   true, true, "WORKING · 2 CHATS"),
               "STALE") == 0);
  check("missing quota wins over stale and agent context",
        strcmp(usage_presenter_quota_status_text(
                   false, true, "WORKING · 2 CHATS"),
               "NO DATA") == 0);

  tk_tokens forecasts = {0};
  forecasts.claude_week = limit(47, 300, 7);
  forecasts.codex_week = limit(35, 2210, 5);
  forecasts.claude_forecast.state = TK_FORECAST_AT_RESET;
  forecasts.claude_forecast.has_pct_at_reset = 1;
  forecasts.claude_forecast.pct_at_reset = 85;
  forecasts.claude_forecast.has_pace_factor = 1;
  forecasts.claude_forecast.pace_factor = 1.4;
  forecasts.codex_forecast.state = TK_FORECAST_EXHAUSTS;
  forecasts.codex_forecast.has_at_epoch = 1;
  forecasts.codex_forecast.at_epoch = local_epoch(2026, 8, 8, 5, 0);
  forecasts.codex_forecast.has_offset_min = 1;
  forecasts.codex_forecast.offset_min = -540;

  usage_forecast_page_view forecast_page = {0};
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("forecasts contain both providers", forecast_page.row_count == 2);
  check("Claude forecast names the all-model scope",
        strcmp(forecast_page.rows[0].label,
               "CLAUDE · ALL MODELS") == 0);
  check("slow pace asks for action",
        strcmp(forecast_page.rows[0].headline, "SPEED UP") == 0 &&
        strcmp(forecast_page.rows[0].detail,
               "1.4× CURRENT PACE TO MAX OUT") == 0);
  check("early exhaustion leads with timing",
        strcmp(forecast_page.rows[1].headline, "9H EARLY") == 0 &&
        strcmp(forecast_page.rows[1].detail,
               "RUNS OUT SAT 05:00") == 0);

  forecasts.claude_forecast.pace_factor = 1.04;
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("rounded one-times pace is on pace",
        strcmp(forecast_page.rows[0].headline, "ON PACE") == 0 &&
        strcmp(forecast_page.rows[0].detail,
               "≈ CURRENT PACE TO MAX OUT") == 0);

  forecasts.claude_forecast.state = TK_FORECAST_COLLECTING;
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("collecting is truthful",
        strcmp(forecast_page.rows[0].headline, "LEARNING PACE") == 0 &&
        strcmp(forecast_page.rows[0].detail,
               "FORECAST NOT READY") == 0);

  forecasts.codex_forecast.offset_min = -35;
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("sub-hour early forecast uses minutes",
        strcmp(forecast_page.rows[1].headline, "35M EARLY") == 0);

  forecasts.codex_forecast.offset_min = -(25 * 60 + 5);
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("day-scale early forecast keeps remaining hours",
        strcmp(forecast_page.rows[1].headline, "1D 1H EARLY") == 0);

  forecasts.codex_forecast.offset_min = 60;
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("contradictory late exhaustion is unavailable",
        strcmp(forecast_page.rows[1].headline, "UNAVAILABLE") == 0 &&
        strcmp(forecast_page.rows[1].detail,
               "NO RELIABLE FORECAST") == 0);

  forecasts.codex_forecast.offset_min = 0;
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("zero-offset exhaustion is on pace",
        strcmp(forecast_page.rows[1].headline, "ON PACE") == 0 &&
        strcmp(forecast_page.rows[1].detail,
               "RUNS OUT AT RESET") == 0);

  forecasts.codex_forecast.state = TK_FORECAST_UNAVAILABLE;
  usage_presenter_build_forecasts(&forecasts, &forecast_page);
  check("unavailable forecast is explicit",
        strcmp(forecast_page.rows[1].headline, "UNAVAILABLE") == 0 &&
        strcmp(forecast_page.rows[1].detail,
               "NO RELIABLE FORECAST") == 0);

  tk_tokens no_week = {0};
  usage_presenter_build_forecasts(&no_week, &forecast_page);
  check("missing weekly quotas suppress forecasts",
        !forecast_page.rows[0].visible && !forecast_page.rows[1].visible);

  if (failures == 0) {
    printf("OK: all usage presenter tests pass\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
