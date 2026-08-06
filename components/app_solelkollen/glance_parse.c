#include "glance_parse.h"

#include <string.h>

#include "../../third_party/cjson/cJSON.h"

/* Every numeric field must exist and be a number; anything else rejects the
 * whole payload. A half-parsed glance is worse than a stale one. */
static bool num(const cJSON *root, const char *key, double *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item)) return false;
  *out = item->valuedouble;
  return true;
}

bool sg_glance_parse(const char *json, size_t len, sg_glance *out,
                     char *day_out, size_t day_cap) {
  if (!json || !out) return false;
  cJSON *root = cJSON_ParseWithLength(json, len);
  if (!root) return false;

  bool ok = false;
  sg_glance g = {0};
  double v = 0, kr = 0, per_hour = 0, w = 0, ore = 0, level = 0;
  double year_kr = 0, total_kr = 0, installation_kr = 0, payback = 0;

  /* The API's error shape ({"error": "..."}) parses fine as JSON — reject it
   * by contract, not by accident. */
  if (cJSON_GetObjectItemCaseSensitive(root, "error")) goto done;

  if (!num(root, "v", &v) || (int)v != 1) goto done;
  if (!num(root, "kr", &kr)) goto done;
  if (!num(root, "krPerHour", &per_hour)) goto done;
  if (!num(root, "w", &w)) goto done;
  if (!num(root, "ore", &ore)) goto done;
  if (!num(root, "level", &level)) goto done;
  if (!num(root, "yearKr", &year_kr)) goto done;
  if (!num(root, "totalKr", &total_kr)) goto done;
  if (!num(root, "installationKr", &installation_kr)) goto done;
  if (!num(root, "paybackPct", &payback)) goto done;

  g.kr = kr;
  g.kr_per_hour = per_hour;
  g.w = (int)w;
  g.ore = (int)ore;
  g.level = level < 0 ? -1 : (level > 0 ? 1 : 0);
  g.year_kr = (long long)year_kr;
  g.total_kr = (long long)total_kr;
  g.installation_kr = (long long)installation_kr;
  g.payback_pct = payback;

  if (day_out && day_cap > 0) {
    const cJSON *day = cJSON_GetObjectItemCaseSensitive(root, "day");
    if (!cJSON_IsString(day) || strlen(day->valuestring) != 10) goto done;
    strncpy(day_out, day->valuestring, day_cap - 1);
    day_out[day_cap - 1] = '\0';
  }

  *out = g;
  ok = true;

done:
  cJSON_Delete(root);
  return ok;
}

bool sg_sverige_parse(const char *json, size_t len, sg_sverige *out) {
  if (!json || !out) return false;
  cJSON *root = cJSON_ParseWithLength(json, len);
  if (!root) return false;

  bool ok = false;
  sg_sverige s = {0};
  double v = 0, day_gwh = 0;

  if (cJSON_GetObjectItemCaseSensitive(root, "error")) goto done;
  if (!num(root, "v", &v) || (int)v != 1) goto done;
  if (!num(root, "dayGwh", &day_gwh)) goto done;

  const cJSON *day = cJSON_GetObjectItemCaseSensitive(root, "day");
  if (!cJSON_IsString(day) || strlen(day->valuestring) != 10) goto done;
  memcpy(s.day, day->valuestring, 10);
  s.day[10] = '\0';

  /* null är ett giltigt värde här — betyder "eSett saknade totaler", och
   * skiljer sig från ett SAKNAT fält, som är ett kontraktsbrott. */
  const cJSON *share = cJSON_GetObjectItemCaseSensitive(root, "sharePct");
  if (!share) goto done;
  if (cJSON_IsNumber(share)) {
    s.share_pct = share->valuedouble;
    s.has_share = 1;
  } else if (!cJSON_IsNull(share)) {
    goto done;
  }

  s.day_gwh = day_gwh;
  *out = s;
  ok = true;

done:
  cJSON_Delete(root);
  return ok;
}
