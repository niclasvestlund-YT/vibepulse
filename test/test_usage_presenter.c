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
                   true, false, "WORKING · 2 AGENTS"),
               "WORKING · 2 AGENTS") == 0);
  check("retained quota is stale despite agent context",
        strcmp(usage_presenter_quota_status_text(
                   true, true, "WORKING · 2 AGENTS"),
               "STALE") == 0);
  check("missing quota wins over stale and agent context",
        strcmp(usage_presenter_quota_status_text(
                   false, true, "WORKING · 2 AGENTS"),
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

  /* --- value multiple ---------------------------------------------------
   * Each provider gets its own row, its own bar and its own break-even,
   * because each subscription pays for itself or does not on its own. Every
   * state below is asserted on what it REFUSES as much as what it shows. */
  usage_value_page_view value_page;

  tk_tokens value_ok = {0};
  value_ok.value.state = TK_VALUE_OK;
  value_ok.value.has_value_usd = 1;
  value_ok.value.value_usd = 312.0;
  value_ok.value.has_plan_usd = 1;
  value_ok.value.plan_usd = 220.0;
  value_ok.value.has_multiple = 1;
  value_ok.value.multiple = 1.42;
  value_ok.value.cost_configured = 1;
  value_ok.value.has_claude_usd = 1;
  value_ok.value.claude_usd = 280.0;
  value_ok.value.has_claude_plan_usd = 1;
  value_ok.value.claude_plan_usd = 200.0;
  value_ok.value.has_codex_usd = 1;
  value_ok.value.codex_usd = 32.0;
  value_ok.value.has_codex_plan_usd = 1;
  value_ok.value.codex_plan_usd = 20.0;

  usage_presenter_build_value(&value_ok, &value_page);
  check("the hero is the ratio",
        value_page.state == USAGE_VALUE_OK &&
        strcmp(value_page.hero_text, "1.42\u00d7") == 0 &&
        !value_page.hero_is_word);
  /* Without a bar marker nothing says 1x is the threshold, so the page must
     also say which way round the two costs came out, in words. */
  check("the verdict answers the page's actual question",
        strcmp(value_page.verdict, "YOUR PLAN IS CHEAPER") == 0);
  check("the split is one quiet line, not two headline figures",
        strcmp(value_page.attribution,
               "CLAUDE $280  ·  CODEX $32") == 0);
  check("the footer pair is the comparison itself",
        strcmp(value_page.api_cost, "$312") == 0 &&
        strcmp(value_page.paid, "$220") == 0);
  check("break-even is the halfway mark on a fixed scale",
        value_page.show_bar &&
        value_page.break_even_fraction > 0.499 &&
        value_page.break_even_fraction < 0.501 &&
        value_page.bar_fraction > 0.70 && value_page.bar_fraction < 0.72);
  check("both providers count when both costs are declared",
        value_page.row_count == 2 &&
        value_page.rows[0].counted && value_page.rows[1].counted);
  check("segments are each provider's share of the counted value",
        value_page.rows[0].share > 0.89 && value_page.rows[0].share < 0.91 &&
        value_page.rows[1].share > 0.09 && value_page.rows[1].share < 0.11);

  /* The panel bug, as a test: Codex usage with no declared Codex plan must
     NOT be credited against Claude's subscription. That is what turned a
     $100 plan into 110x on the glass. */
  tk_tokens value_undeclared = value_ok;
  value_undeclared.value.has_codex_plan_usd = 0;
  usage_presenter_build_value(&value_undeclared, &value_page);
  check("an undeclared provider is shown but never counted",
        value_page.row_count == 2 &&
        value_page.rows[0].counted && !value_page.rows[1].counted);
  check("an uncounted provider colours no segment",
        value_page.rows[1].share == 0.0);

  tk_tokens value_behind = value_ok;
  value_behind.value.multiple = 0.84;
  usage_presenter_build_value(&value_behind, &value_page);
  check("below break-even the verdict flips",
        strcmp(value_page.verdict, "THE API WOULD BE CHEAPER") == 0 &&
        value_page.bar_fraction < value_page.break_even_fraction);

  /* 0.97x must never round to "1.0" -- that reads as broken even. */
  tk_tokens value_near = value_ok;
  value_near.value.multiple = 0.97;
  usage_presenter_build_value(&value_near, &value_page);
  check("just under break-even keeps two decimals",
        strcmp(value_page.hero_text, "0.97\u00d7") == 0);

  tk_tokens value_big = value_ok;
  value_big.value.multiple = 12.4;
  value_big.value.claude_usd = 2480.0;
  usage_presenter_build_value(&value_big, &value_page);
  check("large multiples drop to one decimal",
        strcmp(value_page.hero_text, "12.4\u00d7") == 0);
  check("a runaway ratio clamps the bar but not the hero",
        value_page.bar_fraction > 0.999);
  check("thousands are comma-grouped behind the dollar sign",
        strstr(value_page.attribution, "$2,480") != NULL);

  tk_tokens value_solo = value_ok;
  value_solo.value.has_codex_usd = 0;
  value_solo.value.codex_usd = 0;
  usage_presenter_build_value(&value_solo, &value_page);
  check("one provider means one row taking the whole fill",
        value_page.row_count == 1 &&
        value_page.rows[0].share > 0.99);

  tk_tokens value_no_plan = {0};
  value_no_plan.value.state = TK_VALUE_NO_PLAN_COST;
  value_no_plan.value.has_value_usd = 1;
  value_no_plan.value.value_usd = 312.0;
  usage_presenter_build_value(&value_no_plan, &value_page);
  check("no denominator is the one state where money is the hero",
        value_page.state == USAGE_VALUE_NO_PLAN_COST &&
        strcmp(value_page.hero_text, "$312") == 0 &&
        !value_page.hero_is_word && !value_page.show_bar &&
        strcmp(value_page.verdict, "SET YOUR PLAN COST") == 0);

  tk_tokens value_partial = {0};
  value_partial.value.state = TK_VALUE_PARTIAL;
  usage_presenter_build_value(&value_partial, &value_page);
  check("partial says unpriced and draws nothing",
        value_page.state == USAGE_VALUE_PARTIAL &&
        strcmp(value_page.hero_text, "UNPRICED") == 0 &&
        value_page.hero_is_word && !value_page.show_bar);

  /* Never an en dash as the hero: at hero size a dash is a bare white
     rectangle and reads as a rendering fault rather than as "unknown". */
  tk_tokens value_absent = {0};
  usage_presenter_build_value(&value_absent, &value_page);
  check("absent value block shows a word, never a giant dash",
        value_page.state == USAGE_VALUE_UNAVAILABLE &&
        strcmp(value_page.hero_text, "NO DATA") == 0 &&
        value_page.hero_is_word && value_page.row_count == 0 &&
        !value_page.show_bar);

  usage_presenter_build_value(NULL, &value_page);
  check("null tokens are safe and show nothing",
        value_page.state == USAGE_VALUE_UNAVAILABLE &&
        value_page.row_count == 0 && !value_page.show_bar);

  check("no state ever claims the money was earned",
        strstr(value_page.verdict, "EARNED") == NULL);

  if (failures == 0) {
    printf("OK: all usage presenter tests pass\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
