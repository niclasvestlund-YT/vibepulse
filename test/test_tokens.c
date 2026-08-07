/*
 * Tokenmätarens parsertester — samma metod som test_parse.c: den riktiga
 * fixturen (samma bytes simulatorn renderar) plus fientliga indata. En
 * fixtur som glider ifrån parsern faller här, på Macen, inte på hyllan.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../components/app_tokens/tokens_parse.h"

static int failures = 0;

static char *read_file(const char *path, size_t *len_out) {
  FILE *f = fopen(path, "rb");
  if (!f) { printf("FAIL kan inte läsa %s\n", path); failures++; return NULL; }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  char *buf = malloc((size_t)n + 1);
  fread(buf, 1, (size_t)n, f);
  buf[n] = '\0';
  fclose(f);
  if (len_out) *len_out = (size_t)n;
  return buf;
}

static void check(const char *what, int cond) {
  if (!cond) { printf("FAIL %s\n", what); failures++; }
}

/* Literaler parsas med sin faktiska längd — en felräknad hårdkodad längd
 * är ett test av testet, inte av parsern. */
#define PARSE(s, out) tk_tokens_parse((s), strlen(s), (out))

/* Minsta giltiga v2-kropp med alla limits null — bas för mutationstesterna. */
#define BASE_NULLS \
  "\"claudeSessionPct\":null,\"claudeSessionResetMin\":null," \
  "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null," \
  "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null," \
  "\"codexSessionPct\":null,\"codexSessionResetMin\":null," \
  "\"codexWeekPct\":null,\"codexWeekResetMin\":null"

#define OPTIONAL_USAGE \
  ",\"claudeModelWeekLabel\":\"FABLE · WEEK\"," \
  "\"claudeModelWeekTodayDeltaPct\":3," \
  "\"claudeWeekTodayDeltaPct\":7," \
  "\"claudeSessionHourDeltaPct\":11," \
  "\"codexWeekTodayDeltaPct\":5," \
  "\"claudeForecastState\":\"at_reset\"," \
  "\"claudeForecastPctAtReset\":85," \
  "\"claudeForecastPaceFactor\":1.4," \
  "\"claudeForecastAt\":null," \
  "\"claudeForecastOffsetMin\":null," \
  "\"codexForecastState\":\"exhausts\"," \
  "\"codexForecastPctAtReset\":null," \
  "\"codexForecastPaceFactor\":null," \
  "\"codexForecastAt\":1800007200," \
  "\"codexForecastOffsetMin\":-540"

int main(void) {
  size_t len;
  char *json;
  tk_tokens t = {0};

  json = read_file(FIXTURES_DIR "/tokens.json", &len);
  if (json) {
    check("fixturen parsar", tk_tokens_parse(json, len, &t));
    check("dayTokens", t.day_tokens == 219108100.0);
    check("takt", t.day_tokens_per_hour == 56524317.0);
    check("sessioner", t.day_sessions == 6);
    check("månaden", t.month_tokens == 610000000.0);
    /* Bildfixturen är den godkända statiska AMOLED-layoutens 73/47/21. */
    check("claude session", t.claude_session.has_pct
          && t.claude_session.pct == 21.0
          && t.claude_session.has_reset
          && t.claude_session.reset_min == 256);
    check("claude vecka", t.claude_week.has_pct && t.claude_week.pct == 47.0
          && t.claude_week.reset_min == 9120);
    check("claude fable-vecka", t.claude_model_week.has_pct
          && t.claude_model_week.pct == 73.0
          && t.has_claude_model_week_label);
    check("codex session null", !t.codex_session.has_pct
          && !t.codex_session.has_reset);
    check("codex vecka", t.codex_week.has_pct && t.codex_week.pct == 35.0
          && t.codex_week.reset_min == 2317);
    free(json);
  }

  /* En vilande dag med alla limits null är giltig: nollor och streck är
   * ärliga när inget brunnit och inga källor svarar. */
  check("nollor + null ok",
        PARSE("{\"v\":2,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0," BASE_NULLS "}", &t)
        && t.day_tokens == 0 && !t.claude_session.has_pct);

  check("valfria usagefält parsar",
        PARSE("{\"v\":2,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0," BASE_NULLS
              OPTIONAL_USAGE "}", &t));
  check("modellquotans etikett",
        t.has_claude_model_week_label &&
        strcmp(t.claude_model_week_label, "FABLE · WEEK") == 0);
  check("modellveckans dagsdelta",
        t.claude_model_week.has_delta &&
        t.claude_model_week.delta_pct == 3.0);
  check("allveckans dagsdelta",
        t.claude_week.has_delta && t.claude_week.delta_pct == 7.0);
  check("femtimmars timdelta",
        t.claude_session.has_delta &&
        t.claude_session.delta_pct == 11.0);
  check("Codex dagsdelta",
        t.codex_week.has_delta && t.codex_week.delta_pct == 5.0);
  check("Claude prognos vid reset",
        t.claude_forecast.state == TK_FORECAST_AT_RESET &&
        t.claude_forecast.has_pct_at_reset &&
        t.claude_forecast.pct_at_reset == 85 &&
        t.claude_forecast.has_pace_factor &&
        t.claude_forecast.pace_factor == 1.4);
  check("Codex prognos tar slut",
        t.codex_forecast.state == TK_FORECAST_EXHAUSTS &&
        t.codex_forecast.has_at_epoch &&
        t.codex_forecast.at_epoch == 1800007200LL &&
        t.codex_forecast.has_offset_min &&
        t.codex_forecast.offset_min == -540);

  check("null valfria fält accepteras",
        PARSE("{\"v\":2,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0," BASE_NULLS ","
              "\"claudeModelWeekLabel\":null,"
              "\"claudeWeekTodayDeltaPct\":null,"
              "\"claudeForecastState\":null}", &t) &&
              !t.has_claude_model_week_label &&
              !t.claude_week.has_delta &&
              t.claude_forecast.state == TK_FORECAST_UNAVAILABLE);

  check("trasigt valfritt delta underkänner inte usage",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeWeekTodayDeltaPct\":\"fel\"}", &t) &&
              t.day_tokens == 1 && !t.claude_week.has_delta);
  check("för lång valfri etikett ignoreras",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeModelWeekLabel\":"
              "\"12345678901234567\"}", &t) &&
              !t.has_claude_model_week_label);
  check("okänd prognos underkänner inte usage",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeForecastState\":\"mystery\","
              "\"claudeForecastPctAtReset\":85}", &t) &&
              t.claude_forecast.state == TK_FORECAST_UNAVAILABLE &&
              !t.claude_forecast.has_pct_at_reset);

  /* Fientliga indata: allt som inte är hela kontraktet avvisas utan att
   * röra utdata. */
  tk_tokens before = t;
  check("error-formen avvisas",
        !PARSE("{\"error\":\"scan failed\"}", &t));
  check("gammal version avvisas",
        !PARSE("{\"v\":1,\"dayTokens\":1,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t));
  check("saknat volymfält avvisas",
        !PARSE("{\"v\":2,\"dayTokens\":1," BASE_NULLS "}", &t));
  check("saknat limitfält avvisas",
        !PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1}", &t));
  check("negativ volym avvisas",
        !PARSE("{\"v\":2,\"dayTokens\":-5,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t));
  check("negativ limit avvisas",
        !PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1,"
               "\"claudeSessionPct\":-3,\"claudeSessionResetMin\":null,"
               "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
               "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
               "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
               "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t));
  check("sträng i talfält avvisas",
        !PARSE("{\"v\":2,\"dayTokens\":\"48\",\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t));
  check("trunkerad avvisas", !PARSE("{\"v\":2,\"dayTok", &t));
  check("html avvisas", !PARSE("<html>502</html>", &t));
  check("avvisning rör inte utdata", memcmp(&before, &t, sizeof t) == 0);

  if (failures == 0) { printf("OK: alla tokens-tester gröna\n"); return 0; }
  printf("%d test föll\n", failures);
  return 1;
}
