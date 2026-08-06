#include "tokens_parse.h"

#include "../../third_party/cjson/cJSON.h"

/* Samma kontraktshållning som Solelkollens parser: varje numeriskt fält
 * måste finnas och vara ett tal, annars avvisas hela payloaden. En halv-
 * parsead mätare är värre än en gammal. */
static bool num(const cJSON *root, const char *key, double *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item)) return false;
  *out = item->valuedouble;
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

  if (!num(root, "v", &v) || (int)v != 1) goto done;
  if (!num(root, "dayTokens", &day)) goto done;
  if (!num(root, "dayTokensPerHour", &per_hour)) goto done;
  if (!num(root, "daySessions", &sessions)) goto done;
  if (!num(root, "monthTokens", &month)) goto done;

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
