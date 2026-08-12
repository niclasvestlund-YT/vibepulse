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

static void check_rejected_untouched(const char *what, const char *json,
                                     tk_tokens *out) {
  tk_tokens before;
  char preserved[160];
  memcpy(&before, out, sizeof before);
  check(what, !PARSE(json, out));
  snprintf(preserved, sizeof preserved, "%s preserves output", what);
  check(preserved, memcmp(&before, out, sizeof before) == 0);
}

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
        && t.day_tokens == 0 && !t.claude_session.has_pct &&
        !t.claude_session.stale && !t.claude_week.stale &&
        !t.claude_model_week.stale && !t.codex_session.stale &&
        !t.codex_week.stale);

  check("stale true requires and accepts paired percentages",
        PARSE("{\"v\":2,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0,"
              "\"claudeSessionPct\":null,\"claudeSessionResetMin\":null,"
              "\"claudeWeekPct\":47,\"claudeWeekResetMin\":120,"
              "\"claudeModelWeekPct\":73,"
              "\"claudeModelWeekResetMin\":240,"
              "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
              "\"codexWeekPct\":35,\"codexWeekResetMin\":360,"
              "\"claudeWeekStale\":true,"
              "\"claudeModelWeekStale\":true,"
              "\"codexWeekStale\":true}", &t) &&
              t.claude_week.stale && t.claude_model_week.stale &&
              t.codex_week.stale && !t.claude_session.stale &&
              !t.codex_session.stale);

  check("stale false accepts null percentages",
        PARSE("{\"v\":2,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0," BASE_NULLS ","
              "\"claudeWeekStale\":false,"
              "\"claudeModelWeekStale\":false,"
              "\"codexWeekStale\":false}", &t) &&
              !t.claude_week.stale && !t.claude_model_week.stale &&
              !t.codex_week.stale);

  check("unpublished session stale keys never set provenance",
        PARSE("{\"v\":2,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0," BASE_NULLS ","
              "\"claudeSessionStale\":true,"
              "\"codexSessionStale\":true}", &t) &&
              !t.claude_session.stale && !t.codex_session.stale);

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
  check("kontrolltecken i valfri etikett ignoreras",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeModelWeekLabel\":\"BAD\\u001fLABEL\"}", &t) &&
              !t.has_claude_model_week_label);
  check("NUL-kontroll i valfri etikett ignoreras utan att fälla usage",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeModelWeekLabel\":\"GOOD\\u0000BAD\"}", &t) &&
              t.day_tokens == 1 && !t.has_claude_model_week_label);
  check("decoy-värde kan inte kringgå NUL-kontrollen",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"decoy\":\"claudeModelWeekLabel\","
              "\"claudeModelWeekLabel\":\"GOOD\\u0000BAD\"}", &t) &&
              t.day_tokens == 1 && !t.has_claude_model_week_label);
  check("literal backslash-u0000 är inte en NUL-kontroll",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeModelWeekLabel\":\"GOOD\\\\u0000BAD\"}", &t) &&
              t.has_claude_model_week_label &&
              strcmp(t.claude_model_week_label, "GOOD\\u0000BAD") == 0);
  check("Unicode-kontrolltecken i valfri etikett ignoreras",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeModelWeekLabel\":\"BAD\\u0085LABEL\"}", &t) &&
              !t.has_claude_model_week_label);
  check("ogiltig UTF-8 i valfri etikett ignoreras",
        PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
              "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS ","
              "\"claudeModelWeekLabel\":\"\xC3\x28\"}", &t) &&
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
  check_rejected_untouched("error-formen avvisas",
                           "{\"error\":\"scan failed\"}", &t);
  check_rejected_untouched(
      "gammal version avvisas",
      "{\"v\":1,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "fraktionell version avvisas",
      "{\"v\":2.9,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "oändlig version avvisas",
      "{\"v\":1e999,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched("saknat volymfält avvisas",
                           "{\"v\":2,\"dayTokens\":1," BASE_NULLS "}",
                           &t);
  check_rejected_untouched(
      "saknat limitfält avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1}", &t);
  check_rejected_untouched(
      "negativ volym avvisas",
      "{\"v\":2,\"dayTokens\":-5,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "oändlig dayTokens avvisas",
      "{\"v\":2,\"dayTokens\":1e999,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "oändlig dayTokensPerHour avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":1e999,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "oändlig monthTokens avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1e999," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "fraktionella sessioner avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1.5,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "för många sessioner avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":2147483648,\"monthTokens\":1," BASE_NULLS "}",
      &t);
  check_rejected_untouched(
      "oändligt sessionsantal avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1e999,\"monthTokens\":1," BASE_NULLS "}", &t);
  check_rejected_untouched(
      "negativ limit avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1,"
      "\"claudeSessionPct\":-3,\"claudeSessionResetMin\":null,"
      "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
      "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
      "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
      "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t);
  check_rejected_untouched(
      "limit över hundra avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1,"
      "\"claudeSessionPct\":101,\"claudeSessionResetMin\":null,"
      "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
      "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
      "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
      "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t);
  check_rejected_untouched(
      "oändlig limit avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1,"
      "\"claudeSessionPct\":1e999,\"claudeSessionResetMin\":null,"
      "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
      "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
      "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
      "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t);
  check_rejected_untouched(
      "fraktionell reset avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1,"
      "\"claudeSessionPct\":3,\"claudeSessionResetMin\":1.5,"
      "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
      "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
      "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
      "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t);
  check_rejected_untouched(
      "för stor reset avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1,"
      "\"claudeSessionPct\":3,\"claudeSessionResetMin\":2147483648,"
      "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
      "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
      "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
      "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t);
  check_rejected_untouched(
      "oändlig reset avvisas",
      "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1,"
      "\"claudeSessionPct\":3,\"claudeSessionResetMin\":1e999,"
      "\"claudeWeekPct\":null,\"claudeWeekResetMin\":null,"
      "\"claudeModelWeekPct\":null,\"claudeModelWeekResetMin\":null,"
      "\"codexSessionPct\":null,\"codexSessionResetMin\":null,"
      "\"codexWeekPct\":null,\"codexWeekResetMin\":null}", &t);
  check_rejected_untouched(
      "sträng i talfält avvisas",
      "{\"v\":2,\"dayTokens\":\"48\",\"dayTokensPerHour\":0,"
      "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS "}", &t);

  const char *stale_keys[] = {
      "claudeWeekStale", "claudeModelWeekStale", "codexWeekStale",
  };
  const char *nonbooleans[] = {"null", "\"true\"", "1", "{}", "[]"};
  for (size_t key = 0; key < sizeof stale_keys / sizeof stale_keys[0]; key++) {
    for (size_t value = 0;
         value < sizeof nonbooleans / sizeof nonbooleans[0]; value++) {
      char payload[1024];
      char name[120];
      snprintf(payload, sizeof payload,
               "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS
               ",\"%s\":%s}", stale_keys[key], nonbooleans[value]);
      snprintf(name, sizeof name, "%s rejects non-boolean %s",
               stale_keys[key], nonbooleans[value]);
      check_rejected_untouched(name, payload, &t);
    }
  }

  for (size_t key = 0; key < sizeof stale_keys / sizeof stale_keys[0]; key++) {
    char payload[1024];
    char name[120];
    snprintf(payload, sizeof payload,
             "{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
             "\"daySessions\":1,\"monthTokens\":1," BASE_NULLS
             ",\"%s\":true}", stale_keys[key]);
    snprintf(name, sizeof name, "%s rejects true with null percentage",
             stale_keys[key]);
    check_rejected_untouched(name, payload, &t);
  }

  check_rejected_untouched("trunkerad avvisas", "{\"v\":2,\"dayTok", &t);
  check_rejected_untouched("html avvisas", "<html>502</html>", &t);

  if (failures == 0) { printf("OK: alla tokens-tester gröna\n"); return 0; }
  printf("%d test föll\n", failures);
  return 1;
}
