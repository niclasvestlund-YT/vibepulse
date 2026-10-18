#include "grok_status_parse.h"

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
  int depth = 0;
  for (size_t i = 0; i < len; i++) {
    unsigned char byte = (unsigned char)json[i];
    if (!in_string) {
      if (byte == '"') in_string = true;
      /* Bound cJSON recursion to root object -> bots array -> bot object. */
      else if (byte == '{' || byte == '[') {
        if (++depth > 3) return false;
      } else if (byte == '}' || byte == ']') {
        if (--depth < 0) return false;
      }
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
  return !in_string && !escaped && depth == 0;
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
  if (len >= cap) return false;
  for (size_t i = 0; i < len; i++) {
    unsigned char ch = (unsigned char)item->valuestring[i];
    if (ch < 0x20 || ch > 0x7e) return false;
  }
  memcpy(out, item->valuestring, len + 1);
  return true;
}

static bool member(const char *value, const char *const *values) {
  for (unsigned i = 0; values[i]; i++) if (!strcmp(value, values[i])) return true;
  return false;
}
bool tk_grok_status_parse(const char *json, size_t len, tk_grok_status *out) {
  if (!json || !out || !len || !lexical_guard(json, len)) return false;
  const char *end = NULL;
  cJSON *root = cJSON_ParseWithLengthOpts(json, len, &end, false);
  static const char *const fields[] = {"v","enabled","source","stale","ageSeconds",
    "spentCents","limitCents","prepaidCents","usedPct","resetSeconds","label",
    "active","doneToday","bots","windowSeconds","estimated","planUsed","planLimit","planName",NULL};
  static const char *const sources[] = {"xai","openrouter","plan","bots","none",NULL};
  static const char *const states[] = {"working","waiting","done","idle","error",NULL};
  static const char *const bot_fields[] = {"name","state","activity",NULL};
  tk_grok_status parsed = {0};
  int32_t v;
  bool ok = root && trailing_whitespace(json,len,end) && exact_unique_fields(root,fields) &&
    exact_int32(cJSON_GetObjectItemCaseSensitive(root,"v"),&v) && v == 1;
  cJSON *enabled = cJSON_GetObjectItemCaseSensitive(root,"enabled");
  ok = ok && cJSON_IsBool(enabled);
  if (ok && !cJSON_IsTrue(enabled)) {
    ok = cJSON_GetArraySize(root) == 2;
  } else if (ok) {
    parsed.enabled = true;
    cJSON *stale = cJSON_GetObjectItemCaseSensitive(root,"stale");
    ok = cJSON_IsBool(stale) && copy_ascii(cJSON_GetObjectItemCaseSensitive(root,"source"),parsed.source,sizeof parsed.source) && member(parsed.source,sources);
    parsed.stale = cJSON_IsTrue(stale);
    const char *keys[] = {"ageSeconds","spentCents","limitCents","prepaidCents","usedPct","resetSeconds","planUsed","planLimit"};
    int32_t *nums[] = {&parsed.age_seconds,&parsed.spent_cents,&parsed.limit_cents,&parsed.prepaid_cents,&parsed.used_pct,&parsed.reset_seconds,&parsed.plan_used,&parsed.plan_limit};
    bool *flags[] = {&parsed.has_age,&parsed.has_spent,&parsed.has_limit,&parsed.has_prepaid,&parsed.has_used,&parsed.has_reset,&parsed.has_plan_used,&parsed.has_plan_limit};
    for (unsigned i=0; ok && i<8; i++) {
      cJSON *item = cJSON_GetObjectItemCaseSensitive(root,keys[i]);
      if (item) { ok = exact_int32(item,nums[i]); *flags[i] = ok; }
    }
    cJSON *window = cJSON_GetObjectItemCaseSensitive(root,"windowSeconds");
    if (ok && window) {
      ok = exact_int32(window,&parsed.window_seconds) &&
        parsed.window_seconds >= 1 && parsed.window_seconds <= 31*86400;
      parsed.has_window = ok;
    }
    ok = ok && parsed.used_pct <= 100 && (!parsed.has_plan_limit || parsed.plan_limit >= 1);
    cJSON *estimated = cJSON_GetObjectItemCaseSensitive(root,"estimated");
    if (ok && estimated) {
      ok = cJSON_IsBool(estimated);
      parsed.has_estimated = ok;
      parsed.estimated = cJSON_IsTrue(estimated);
    }
    cJSON *plan_name = cJSON_GetObjectItemCaseSensitive(root,"planName");
    if (ok && plan_name) ok = copy_ascii(plan_name,parsed.plan_name,sizeof parsed.plan_name);
    cJSON *label = cJSON_GetObjectItemCaseSensitive(root,"label");
    if (ok && label) ok = copy_ascii(label,parsed.label,sizeof parsed.label);
    ok = ok && exact_int32(cJSON_GetObjectItemCaseSensitive(root,"active"),&parsed.active) &&
      exact_int32(cJSON_GetObjectItemCaseSensitive(root,"doneToday"),&parsed.done_today);
    cJSON *bots = cJSON_GetObjectItemCaseSensitive(root,"bots");
    ok = ok && cJSON_IsArray(bots) && cJSON_GetArraySize(bots)<=4;
    for (cJSON *bot = ok ? bots->child : NULL; ok && bot; bot=bot->next) {
      tk_grok_bot *b = &parsed.bots[parsed.bot_count++];
      ok = exact_unique_fields(bot,bot_fields) && copy_ascii(cJSON_GetObjectItemCaseSensitive(bot,"name"),b->name,sizeof b->name) && b->name[0] &&
        copy_ascii(cJSON_GetObjectItemCaseSensitive(bot,"state"),b->state,sizeof b->state) && member(b->state,states);
      cJSON *activity = cJSON_GetObjectItemCaseSensitive(bot,"activity");
      if (ok && activity) ok = copy_ascii(activity,b->activity,sizeof b->activity);
    }
  }
  cJSON_Delete(root);
  if (ok) *out = parsed;
  return ok;
}
