#include <stdio.h>
#include <string.h>

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
  check("week delta is English", strcmp(hero.quota.delta_text, "+3 TODAY") == 0);

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
  check("forecasts use stable English copy",
        forecast_page.row_count == 2 &&
        strcmp(forecast_page.rows[0].label, "CLAUDE · WEEKLY") == 0 &&
        strcmp(forecast_page.rows[0].headline, "85% AT RESET") == 0 &&
        strcmp(forecast_page.rows[0].detail, "INCREASE 1.4x TO MAX OUT") == 0 &&
        strcmp(forecast_page.rows[1].headline, "FORECAST UNAVAILABLE") == 0);

  if (failures == 0) {
    printf("OK: all usage presenter tests pass\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
