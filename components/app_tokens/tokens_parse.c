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
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) return false;
  *out = item->valuedouble;
  return true;
}

/* Limit-fälten: null är ett GILTIGT värde (källan otillgänglig — has 0),
 * men ett SAKNAT fält är ett kontraktsbrott, och negativt är en lögn.
 * Samma regel som sharePct i Sverige-parsern. */
static bool pct_or_null(const cJSON *root, const char *key,
                        double *out, int *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!item) return false;
  if (cJSON_IsNumber(item)) {
    if (!isfinite(item->valuedouble) || item->valuedouble < 0 ||
        item->valuedouble > 100) {
      return false;
    }
    *out = item->valuedouble;
    *has_out = 1;
    return true;
  }
  return cJSON_IsNull(item); /* has_out lämnas 0 */
}

static bool reset_or_null(const cJSON *root, const char *key,
                          int *out, int *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!item) return false;
  if (cJSON_IsNumber(item)) {
    double value = item->valuedouble;
    if (!isfinite(value) || value < 0 || value > INT_MAX ||
        trunc(value) != value) {
      return false;
    }
    *out = (int)value;
    *has_out = 1;
    return true;
  }
  return cJSON_IsNull(item);
}

/* En limit = ett procentfält + ett reset-fält, t.ex. "claudeSessionPct" +
 * "claudeSessionResetMin". */
static bool limit_pair(const cJSON *root, const char *pct_key,
                       const char *reset_key, tk_limit *out) {
  if (!pct_or_null(root, pct_key, &out->pct, &out->has_pct)) return false;
  if (!reset_or_null(root, reset_key, &out->reset_min, &out->has_reset)) {
    return false;
  }
  return true;
}

static bool optional_stale(const cJSON *root, const char *key,
                           tk_limit *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!item) return true;
  if (!cJSON_IsBool(item)) return false;
  out->stale = cJSON_IsTrue(item) ? 1 : 0;
  return !out->stale || out->has_pct;
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

static bool unicode_control(uint32_t codepoint) {
  return codepoint <= 0x1f || (codepoint >= 0x7f && codepoint <= 0x9f) ||
         (codepoint >= 0x200b && codepoint <= 0x200f) ||
         (codepoint >= 0x2028 && codepoint <= 0x202e) ||
         (codepoint >= 0x2060 && codepoint <= 0x206f) ||
         codepoint == 0xfeff;
}

static bool valid_utf8_label(const unsigned char *source, size_t capacity,
                             size_t *length_out) {
  size_t offset = 0;
  while (source[offset]) {
    uint32_t codepoint = 0;
    size_t width = 0;
    unsigned char first = source[offset];
    if (first < 0x80) {
      codepoint = first;
      width = 1;
    } else if (first >= 0xc2 && first <= 0xdf) {
      codepoint = first & 0x1f;
      width = 2;
    } else if (first >= 0xe0 && first <= 0xef) {
      codepoint = first & 0x0f;
      width = 3;
    } else if (first >= 0xf0 && first <= 0xf4) {
      codepoint = first & 0x07;
      width = 4;
    } else {
      return false;
    }
    if (offset + width >= capacity) return false;
    for (size_t index = 1; index < width; index++) {
      unsigned char next = source[offset + index];
      if ((next & 0xc0) != 0x80) return false;
      codepoint = (codepoint << 6) | (next & 0x3f);
    }
    if ((width == 2 && codepoint < 0x80) ||
        (width == 3 && codepoint < 0x800) ||
        (width == 4 && codepoint < 0x10000) ||
        (codepoint >= 0xd800 && codepoint <= 0xdfff) ||
        codepoint > 0x10ffff || unicode_control(codepoint)) {
      return false;
    }
    offset += width;
  }
  *length_out = offset;
  return true;
}

/* cJSON representerar ett avkodat \u0000 som C-strängens terminator. Par av
 * escape-backslash hoppas över, så texten "\\\\u0000" är fortsatt text. */
static bool raw_string_has_nul_escape(const char *json, size_t start,
                                      size_t end) {
  for (size_t offset = start; offset < end; offset++) {
    if (json[offset] != '\\' || offset + 1 >= end) continue;
    if (json[offset + 1] == 'u' && offset + 5 < end &&
        memcmp(json + offset + 2, "0000", 4) == 0) {
      return true;
    }
    offset++;
  }
  return false;
}

typedef struct {
  bool nul_key;
  bool nul_string_value;
} raw_json_string_scan;

/* Körs först efter att cJSON godkänt grammatiken. Ett strängtoken vars nästa
 * icke-blanktecken är kolon är då entydigt en medlemsnyckel. */
static bool scan_raw_json_strings(const char *json, size_t length,
                                  raw_json_string_scan *out) {
  memset(out, 0, sizeof *out);
  for (size_t offset = 0; offset < length; offset++) {
    if (json[offset] != '"') continue;
    size_t start = offset + 1;
    size_t end = start;
    while (end < length && json[end] != '"') {
      if (json[end] == '\\') {
        if (end + 1 >= length) return false;
        end += 2;
      } else {
        end++;
      }
    }
    if (end >= length) return false;

    if (raw_string_has_nul_escape(json, start, end)) {
      size_t next = end + 1;
      while (next < length &&
             (json[next] == ' ' || json[next] == '\t' ||
              json[next] == '\r' || json[next] == '\n')) {
        next++;
      }
      if (next < length && json[next] == ':')
        out->nul_key = true;
      else
        out->nul_string_value = true;
    }
    offset = end;
  }
  return true;
}

static bool known_top_level_key(const char *key) {
  static const char *const keys[] = {
      "error", "v", "dayTokens", "dayTokensPerHour", "daySessions",
      "monthTokens", "claudeSessionPct", "claudeSessionResetMin",
      "claudeWeekPct", "claudeWeekResetMin", "claudeModelWeekPct",
      "claudeModelWeekResetMin", "codexSessionPct", "codexSessionResetMin",
      "codexWeekPct", "codexWeekResetMin", "claudeWeekStale",
      "claudeModelWeekStale", "codexWeekStale", "claudeModelWeekLabel",
      "claudeModelWeekTodayDeltaPct", "claudeWeekTodayDeltaPct",
      "claudeSessionHourDeltaPct", "codexWeekTodayDeltaPct",
      "claudeForecastState", "claudeForecastPctAtReset",
      "claudeForecastPaceFactor", "claudeForecastAt",
      "claudeForecastOffsetMin", "codexForecastState",
      "codexForecastPctAtReset", "codexForecastPaceFactor",
      "codexForecastAt", "codexForecastOffsetMin",
  };
  for (size_t index = 0; index < sizeof keys / sizeof keys[0]; index++) {
    if (strcmp(key, keys[index]) == 0) return true;
  }
  return false;
}

static bool has_duplicate_known_top_level_key(const cJSON *root) {
  if (!cJSON_IsObject(root)) return false;
  for (const cJSON *item = root->child; item; item = item->next) {
    if (!item->string || !known_top_level_key(item->string)) continue;
    for (const cJSON *prior = root->child; prior != item;
         prior = prior->next) {
      if (prior->string && strcmp(prior->string, item->string) == 0) {
        return true;
      }
    }
  }
  return false;
}

static void optional_label(const cJSON *root, bool trust_strings,
                           const char *key, char *out, size_t capacity,
                           int *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsString(item) || !item->valuestring) return;
  if (!trust_strings) return;
  const unsigned char *source = (const unsigned char *)item->valuestring;
  size_t length = 0;
  if (!valid_utf8_label(source, capacity, &length)) return;
  memcpy(out, source, length + 1);
  *has_out = 1;
}

static bool optional_integer(const cJSON *root, const char *key,
                             double minimum, double maximum_exclusive,
                             int64_t *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
      item->valuedouble < minimum ||
      item->valuedouble >= maximum_exclusive ||
      trunc(item->valuedouble) != item->valuedouble) {
    return false;
  }
  *out = (int64_t)item->valuedouble;
  return true;
}

static void optional_forecast(const cJSON *root, const char *prefix,
                              bool trust_strings, tk_forecast *out) {
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
  if (!trust_strings || !cJSON_IsString(state) || !state->valuestring) return;
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
    if (!optional_integer(root, at_key, 0.0, 0x1p63, &at) ||
        !optional_integer(root, offset_key, (double)INT_MIN,
                          (double)INT_MAX + 1.0, &offset)) {
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
  if (memchr(json, '\0', len)) return false;
  cJSON *root = cJSON_ParseWithLength(json, len);
  if (!root) return false;

  bool ok = false;
  tk_tokens t = {0};
  double v = 0, day = 0, per_hour = 0, sessions = 0, month = 0;
  raw_json_string_scan strings = {0};
  if (!scan_raw_json_strings(json, len, &strings) || strings.nul_key ||
      has_duplicate_known_top_level_key(root)) {
    goto done;
  }
  bool trust_optional_strings = !strings.nul_string_value;

  /* Tjänstens felform ({"error": "..."}) parsar fint som JSON — avvisa den
   * per kontrakt, inte av misstag. */
  if (cJSON_GetObjectItemCaseSensitive(root, "error")) goto done;

  if (!num(root, "v", &v) || v != 2.0) goto done;
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

  if (!optional_stale(root, "claudeWeekStale", &t.claude_week)) goto done;
  if (!optional_stale(root, "claudeModelWeekStale",
                      &t.claude_model_week)) goto done;
  if (!optional_stale(root, "codexWeekStale", &t.codex_week)) goto done;

  optional_label(root, trust_optional_strings, "claudeModelWeekLabel",
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
  optional_forecast(root, "claude", trust_optional_strings,
                    &t.claude_forecast);
  optional_forecast(root, "codex", trust_optional_strings,
                    &t.codex_forecast);

  /* Inget på den här mätaren kan ärligt vara negativt — ett minustecken är
   * en lögn med ett stavfel (samma regel som sv_group_ll). */
  if (day < 0 || per_hour < 0 || sessions < 0 || sessions > INT_MAX ||
      trunc(sessions) != sessions || month < 0) {
    goto done;
  }

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
