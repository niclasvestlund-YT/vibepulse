#include "lovable_status_parse.h"

#include <math.h>
#include <stdint.h>
#include <string.h>

#include "../../third_party/cjson/cJSON.h"

/* Same strictness as github_status_parse.c: exact field allowlist, no
 * duplicates, no trailing bytes, no decoded NUL. A rejected payload keeps
 * the last good page on the glass instead of drawing a guess. */

static bool whitespace(unsigned char byte) {
  return byte == ' ' || byte == '\t' || byte == '\n' || byte == '\r';
}

static bool lexical_guard(const char *json, size_t len) {
  bool in_string = false;
  bool escaped = false;
  for (size_t i = 0; i < len; i++) {
    unsigned char byte = (unsigned char)json[i];
    if (!in_string) {
      if (byte == '"') in_string = true;
      else if (byte < 0x20 && !whitespace(byte)) return false;
      continue;
    }
    if (escaped) {
      escaped = false;
      if (byte == 'u' && i + 4 < len && json[i + 1] == '0' &&
          json[i + 2] == '0' && json[i + 3] == '0' && json[i + 4] == '0')
        return false;
    } else if (byte == '\\') {
      escaped = true;
    } else if (byte == '"') {
      in_string = false;
    } else if (byte < 0x20) {
      return false;
    }
  }
  return !in_string && !escaped;
}

static bool trailing_whitespace(const char *json, size_t len,
                                const char *parse_end) {
  if (!parse_end || parse_end < json || parse_end > json + len) return false;
  for (const char *c = parse_end; c < json + len; c++)
    if (!whitespace((unsigned char)*c)) return false;
  return true;
}

static bool exact_unique_fields(const cJSON *object,
                                const char *const *allowed) {
  if (!cJSON_IsObject(object)) return false;
  for (const cJSON *item = object->child; item; item = item->next) {
    bool known = false;
    for (size_t i = 0; allowed[i]; i++)
      if (item->string && strcmp(item->string, allowed[i]) == 0) known = true;
    if (!known) return false;
    for (const cJSON *o = item->next; o; o = o->next)
      if (o->string && strcmp(item->string, o->string) == 0) return false;
  }
  return true;
}

static bool exact_int32(const cJSON *item, int32_t *out) {
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
      item->valuedouble < 0 || item->valuedouble > INT32_MAX ||
      floor(item->valuedouble) != item->valuedouble)
    return false;
  *out = (int32_t)item->valuedouble;
  return true;
}

/* Printable ASCII only: the panel fonts carry nothing else. */
static bool copy_ascii(const cJSON *item, char *out, size_t cap) {
  if (!cJSON_IsString(item) || !item->valuestring) return false;
  size_t len = strlen(item->valuestring);
  if (len == 0 || len >= cap) return false;
  for (size_t i = 0; i < len; i++) {
    unsigned char ch = (unsigned char)item->valuestring[i];
    if (ch < 0x20 || ch > 0x7e) return false;
  }
  memcpy(out, item->valuestring, len + 1);
  return true;
}

static bool valid_plan(const char *plan) {
  for (const char *c = plan; *c; c++) {
    unsigned char ch = (unsigned char)*c;
    if (!((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') ||
          ch == ' ' || ch == '+'))
      return false;
  }
  return true;
}

bool tk_lovable_status_parse(const char *json, size_t len,
                             tk_lovable_status *out) {
  if (!json || !out || len == 0 || !lexical_guard(json, len)) return false;
  const char *parse_end = NULL;
  cJSON *root = cJSON_ParseWithLengthOpts(json, len, &parse_end, false);
  if (!root || !trailing_whitespace(json, len, parse_end)) {
    cJSON_Delete(root);
    return false;
  }

  static const char *const allowed[] = {
      "v", "enabled", "stale", "login", "creditsTenths", "ageSeconds",
      "plan", "workspace", "grantTenths", "resetSeconds", "periodSeconds",
      "dailyCreditsTenths", "dailyGrantTenths",
      NULL,
  };
  tk_lovable_status parsed = {0};
  int32_t version = 0;
  cJSON *enabled = cJSON_GetObjectItemCaseSensitive(root, "enabled");
  bool ok = exact_unique_fields(root, allowed) &&
            exact_int32(cJSON_GetObjectItemCaseSensitive(root, "v"),
                        &version) &&
            version == 1 && cJSON_IsBool(enabled);

  if (ok && !cJSON_IsTrue(enabled)) {
    ok = root->child && root->child->next && root->child->next->next == NULL;
  } else if (ok) {
    parsed.enabled = true;
    cJSON *stale = cJSON_GetObjectItemCaseSensitive(root, "stale");
    cJSON *login = cJSON_GetObjectItemCaseSensitive(root, "login");
    ok = cJSON_IsBool(stale) && (!login || cJSON_IsTrue(login));
    parsed.stale = cJSON_IsTrue(stale);
    parsed.needs_login = login != NULL;

    cJSON *credits = cJSON_GetObjectItemCaseSensitive(root, "creditsTenths");
    cJSON *age = cJSON_GetObjectItemCaseSensitive(root, "ageSeconds");
    if (ok && (credits || age)) {
      ok = credits && age && exact_int32(credits, &parsed.credits_tenths) &&
           exact_int32(age, &parsed.age_seconds);
      parsed.has_data = ok;
    }
    cJSON *plan = cJSON_GetObjectItemCaseSensitive(root, "plan");
    if (ok && plan) {
      ok = parsed.has_data &&
           copy_ascii(plan, parsed.plan, sizeof parsed.plan) &&
           valid_plan(parsed.plan);
      parsed.has_plan = ok;
    }
    cJSON *workspace = cJSON_GetObjectItemCaseSensitive(root, "workspace");
    if (ok && workspace) {
      ok = parsed.has_data &&
           copy_ascii(workspace, parsed.workspace, sizeof parsed.workspace);
      parsed.has_workspace = ok;
    }
    cJSON *grant = cJSON_GetObjectItemCaseSensitive(root, "grantTenths");
    if (ok && grant) {
      ok = parsed.has_data && exact_int32(grant, &parsed.grant_tenths) &&
           parsed.grant_tenths > 0;
      parsed.has_grant = ok;
    }
    cJSON *reset = cJSON_GetObjectItemCaseSensitive(root, "resetSeconds");
    if (ok && reset) {
      ok = parsed.has_data && exact_int32(reset, &parsed.reset_seconds) &&
           parsed.reset_seconds > 0;
      parsed.has_reset = ok;
    }
    cJSON *period = cJSON_GetObjectItemCaseSensitive(root, "periodSeconds");
    if (ok && period) {
      ok = parsed.has_reset && exact_int32(period, &parsed.period_seconds) &&
           parsed.period_seconds >= parsed.reset_seconds;
      parsed.has_period = ok;
    }
    cJSON *daily = cJSON_GetObjectItemCaseSensitive(root,
                                                     "dailyCreditsTenths");
    if (ok && daily) {
      ok = parsed.has_data && exact_int32(daily,
                                          &parsed.daily_credits_tenths);
      parsed.has_daily_credits = ok;
    }
    cJSON *daily_grant = cJSON_GetObjectItemCaseSensitive(
        root, "dailyGrantTenths");
    if (ok && daily_grant) {
      ok = parsed.has_data && exact_int32(daily_grant,
                                          &parsed.daily_grant_tenths) &&
           parsed.daily_grant_tenths > 0;
      parsed.has_daily_grant = ok;
    }
  }

  cJSON_Delete(root);
  if (!ok) return false;
  *out = parsed;
  return true;
}
