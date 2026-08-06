#include "tokens_parse.h"

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
