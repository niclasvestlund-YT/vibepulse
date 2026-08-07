#include "tokens_parse.h"

#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../../third_party/cjson/cJSON.h"

/* Samma kontraktshållning som Solelkollens parser: varje obligatoriskt
 * numeriskt fält måste finnas och vara ett tal, annars avvisas hela
 * payloaden. En halvparsead mätare är värre än en gammal. */
static bool num(const cJSON *root, const char *key, double *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item)) return false;
  *out = item->valuedouble;
  return true;
}

/* Limit-fälten: null är ett GILTIGT värde (källan otillgänglig — has 0),
 * men ett SAKNAT fält är ett kontraktsbrott, och negativt är en lögn.
 * Samma regel som sharePct i Sverige-parsern. */
static bool num_or_null(const cJSON *root, const char *key,
                        double *out, int *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!item) return false;
  if (cJSON_IsNumber(item)) {
    if (item->valuedouble < 0) return false;
    *out = item->valuedouble;
    *has_out = 1;
    return true;
  }
  return cJSON_IsNull(item); /* has_out lämnas 0 */
}

/* En limit = ett procentfält + ett reset-fält, t.ex. "claudeSessionPct" +
 * "claudeSessionResetMin". */
static bool limit_pair(const cJSON *root, const char *pct_key,
                       const char *reset_key, tk_limit *out) {
  double reset = 0;
  if (!num_or_null(root, pct_key, &out->pct, &out->has_pct)) return false;
  if (!num_or_null(root, reset_key, &reset, &out->has_reset)) return false;
  out->reset_min = (int)reset;
  return true;
}

static void optional_nonnegative_number(const cJSON *root, const char *key,
                                        double maximum, double *out,
                                        int *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
      item->valuedouble < 0 || item->valuedouble > maximum) {
    return;
  }
  *out = item->valuedouble;
  *has_out = 1;
}

static void optional_label(const cJSON *root, const char *key, char *out,
                           size_t capacity, int *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsString(item) || !item->valuestring) return;
  const unsigned char *source = (const unsigned char *)item->valuestring;
  size_t length = 0;
  while (source[length]) {
    if (source[length] < 0x20 || length + 1 >= capacity) return;
    length++;
  }
  memcpy(out, source, length + 1);
  *has_out = 1;
}

static bool optional_integer(const cJSON *root, const char *key,
                             double minimum, double maximum,
                             int64_t *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
      item->valuedouble < minimum || item->valuedouble > maximum ||
      trunc(item->valuedouble) != item->valuedouble) {
    return false;
  }
  *out = (int64_t)item->valuedouble;
  return true;
}

static void optional_forecast(const cJSON *root, const char *prefix,
                              tk_forecast *out) {
  char state_key[40];
  char pct_key[48];
  char pace_key[48];
  char at_key[40];
  char offset_key[48];
  snprintf(state_key, sizeof state_key, "%sForecastState", prefix);
  snprintf(pct_key, sizeof pct_key, "%sForecastPctAtReset", prefix);
  snprintf(pace_key, sizeof pace_key, "%sForecastPaceFactor", prefix);
  snprintf(at_key, sizeof at_key, "%sForecastAt", prefix);
  snprintf(offset_key, sizeof offset_key, "%sForecastOffsetMin", prefix);

  const cJSON *state = cJSON_GetObjectItemCaseSensitive(root, state_key);
  if (!cJSON_IsString(state) || !state->valuestring) return;
  if (strcmp(state->valuestring, "collecting") == 0) {
    out->state = TK_FORECAST_COLLECTING;
    return;
  }
  if (strcmp(state->valuestring, "unavailable") == 0) return;
  if (strcmp(state->valuestring, "at_reset") == 0) {
    double pct = 0;
    double pace = 0;
    int has_pct = 0;
    int has_pace = 0;
    optional_nonnegative_number(root, pct_key, 100, &pct, &has_pct);
    optional_nonnegative_number(root, pace_key, 1000, &pace, &has_pace);
    if (!has_pct || !has_pace || trunc(pct) != pct || pace <= 0) return;
    out->state = TK_FORECAST_AT_RESET;
    out->pct_at_reset = (int)pct;
    out->pace_factor = pace;
    out->has_pct_at_reset = 1;
    out->has_pace_factor = 1;
    return;
  }
  if (strcmp(state->valuestring, "exhausts") == 0) {
    int64_t at = 0;
    int64_t offset = 0;
    if (!optional_integer(root, at_key, 0, (double)INT64_MAX, &at) ||
        !optional_integer(root, offset_key, INT_MIN, INT_MAX, &offset)) {
      return;
    }
    out->state = TK_FORECAST_EXHAUSTS;
    out->at_epoch = at;
    out->offset_min = (int)offset;
    out->has_at_epoch = 1;
    out->has_offset_min = 1;
  }
}

bool tk_tokens_parse(const char *json, size_t len, tk_tokens *out) {
  if (!json || !out) return false;
  cJSON *root = cJSON_ParseWithLength(json, len);
  if (!root) return false;

  bool ok = false;
  tk_tokens t = {0};
  double v = 0, day = 0, per_hour = 0, sessions = 0, month = 0;

  /* Tjänstens felform ({"error": "..."}) parsar fint som JSON — avvisa den
   * per kontrakt, inte av misstag. */
  if (cJSON_GetObjectItemCaseSensitive(root, "error")) goto done;

  if (!num(root, "v", &v) || (int)v != 2) goto done;
  if (!num(root, "dayTokens", &day)) goto done;
  if (!num(root, "dayTokensPerHour", &per_hour)) goto done;
  if (!num(root, "daySessions", &sessions)) goto done;
  if (!num(root, "monthTokens", &month)) goto done;

  if (!limit_pair(root, "claudeSessionPct", "claudeSessionResetMin",
                  &t.claude_session)) goto done;
  if (!limit_pair(root, "claudeWeekPct", "claudeWeekResetMin",
                  &t.claude_week)) goto done;
  if (!limit_pair(root, "claudeModelWeekPct", "claudeModelWeekResetMin",
                  &t.claude_model_week)) goto done;
  if (!limit_pair(root, "codexSessionPct", "codexSessionResetMin",
                  &t.codex_session)) goto done;
  if (!limit_pair(root, "codexWeekPct", "codexWeekResetMin",
                  &t.codex_week)) goto done;

  optional_label(root, "claudeModelWeekLabel",
                 t.claude_model_week_label,
                 sizeof t.claude_model_week_label,
                 &t.has_claude_model_week_label);
  optional_nonnegative_number(
      root, "claudeModelWeekTodayDeltaPct", 100,
      &t.claude_model_week.delta_pct, &t.claude_model_week.has_delta);
  optional_nonnegative_number(
      root, "claudeWeekTodayDeltaPct", 100,
      &t.claude_week.delta_pct, &t.claude_week.has_delta);
  optional_nonnegative_number(
      root, "claudeSessionHourDeltaPct", 100,
      &t.claude_session.delta_pct, &t.claude_session.has_delta);
  optional_nonnegative_number(
      root, "codexWeekTodayDeltaPct", 100,
      &t.codex_week.delta_pct, &t.codex_week.has_delta);
  optional_forecast(root, "claude", &t.claude_forecast);
  optional_forecast(root, "codex", &t.codex_forecast);

  /* Inget på den här mätaren kan ärligt vara negativt — ett minustecken är
   * en lögn med ett stavfel (samma regel som sv_group_ll). */
  if (day < 0 || per_hour < 0 || sessions < 0 || month < 0) goto done;

  t.day_tokens = day;
  t.day_tokens_per_hour = per_hour;
  t.day_sessions = (int)sessions;
  t.month_tokens = month;

  *out = t;
  ok = true;

done:
  cJSON_Delete(root);
  return ok;
}
