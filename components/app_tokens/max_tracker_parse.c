#include "max_tracker_parse.h"

#include <math.h>
#include <stdint.h>
#include <string.h>

#include "../../third_party/cjson/cJSON.h"

/* Samma kontraktshållning som Solelkollens och tokens-parsern: varje
 * obligatoriskt fält måste finnas, ha rätt typ och ligga i sitt intervall,
 * annars avvisas hela payloaden. En halvparsead historik är värre än en
 * gammal. */
static bool num(const cJSON *root, const char *key, double *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) return false;
  *out = item->valuedouble;
  return true;
}

/* Ett heltalsfält med ett stängt intervall — maxWeeksStreak/maxWeeks/
 * maxDays kan aldrig ärligt överstiga fönstrets egen storlek. */
static bool bounded_int(const cJSON *root, const char *key, double minimum,
                        double maximum, int *out) {
  double value;
  if (!num(root, key, &value)) return false;
  if (value < minimum || value > maximum || trunc(value) != value) return false;
  *out = (int)value;
  return true;
}

/* codingStreakDays: null är GILTIGT (enheten mappar det till -1 = okänt —
 * ingen aktivitet registrerad ännu), men ett SAKNAT fält är ett
 * kontraktsbrott och ett negativt eller brutet tal är en lögn. */
static bool coding_streak(const cJSON *root, const char *key, int *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
  if (!item) return false;
  if (cJSON_IsNull(item)) {
    *out = -1;
    return true;
  }
  if (cJSON_IsNumber(item)) {
    double value = item->valuedouble;
    if (!isfinite(value) || value < 0 || value > INT32_MAX ||
        trunc(value) != value) {
      return false;
    }
    *out = (int)value;
    return true;
  }
  return false;
}

/* avgPeakPct: null är GILTIGT (noll dagar med riktig pct att snitta —
 * aldrig påhittat), men ett SAKNAT fält är ett kontraktsbrott. Samma regel
 * som sharePct i Sverige-parsern och limit-fälten i tokens-kontraktet. */
static bool avg_peak_pct(const cJSON *root, double *out, bool *has_out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, "avgPeakPct");
  if (!item) return false;
  if (cJSON_IsNumber(item)) {
    if (!isfinite(item->valuedouble) || item->valuedouble < 0 ||
        item->valuedouble > 100) {
      return false;
    }
    *out = item->valuedouble;
    *has_out = true;
    return true;
  }
  return cJSON_IsNull(item); /* has_out lämnas false */
}

/* weekMaxed: fast längd TK_MT_WEEKS, varje post 0 eller 1 — samma
 * vänster-till-höger-ordning som days, en post per ISO-vecka. */
static bool parse_week_maxed(const cJSON *root, bool *out) {
  const cJSON *arr = cJSON_GetObjectItemCaseSensitive(root, "weekMaxed");
  if (!cJSON_IsArray(arr) || cJSON_GetArraySize(arr) != TK_MT_WEEKS) {
    return false;
  }
  int index = 0;
  for (const cJSON *item = arr->child; item; item = item->next, index++) {
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) return false;
    double value = item->valuedouble;
    if (value != 0.0 && value != 1.0) return false;
    out[index] = value != 0.0;
  }
  return true;
}

/* days: fast längd TK_MT_DAYS, varje post [pct, lvl]. pct -1..100 (-1 =
 * ingen kvotdata den dagen, ärlig frånvaro; 100 reserverat för en exakt
 * kvotmax). lvl -1..2 är ett OBEROENDE fält satt av servern från volym —
 * lvl kan vara 0-2 även när pct >= 0. Parsern lagrar båda verbatim efter
 * intervallkontroll; det är bara rendering som avgör att grått gäller när
 * pct == -1. */
static bool parse_days(const cJSON *root, tk_mt_day *out) {
  const cJSON *arr = cJSON_GetObjectItemCaseSensitive(root, "days");
  if (!cJSON_IsArray(arr) || cJSON_GetArraySize(arr) != TK_MT_DAYS) {
    return false;
  }
  int index = 0;
  for (const cJSON *pair = arr->child; pair; pair = pair->next, index++) {
    if (!cJSON_IsArray(pair) || cJSON_GetArraySize(pair) != 2) return false;
    const cJSON *pct_item = pair->child;
    const cJSON *lvl_item = pct_item ? pct_item->next : NULL;
    if (!cJSON_IsNumber(pct_item) || !isfinite(pct_item->valuedouble)) {
      return false;
    }
    if (!cJSON_IsNumber(lvl_item) || !isfinite(lvl_item->valuedouble)) {
      return false;
    }
    double pct = pct_item->valuedouble;
    double lvl = lvl_item->valuedouble;
    if (pct < -1 || pct > 100 || trunc(pct) != pct) return false;
    if (lvl < -1 || lvl > 2 || trunc(lvl) != lvl) return false;
    out[index].pct = (int8_t)pct;
    out[index].lvl = (int8_t)lvl;
  }
  return true;
}

static bool plan_label_char_allowed(unsigned char c) {
  return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == ' ';
}

/* planLabel är ett UNDANTAG från "allt avvisar hela dokumentet": en
 * syntaktiskt ogiltig etikett (fel typ, tom, för lång, tecken utanför
 * A-Z/0-9/mellanslag) tappas för sig (has_plan lämnas false) medan resten
 * av providerns och dokumentets fält ändå parsas — samma
 * visningssäkerhetsregel som claudeModelWeekLabel i tokens-kontraktet.
 * trust_strings är false när dokumentet innehåller en NUL-escape i
 * något strängvärde — en avkortad synlig text (t.ex. "PRO" ur
 * "PRO" + NUL + "BAD") kan se giltig ut men döljer data efter den inbäddade
 * nollan, så hela dokumentet får då inte lita på NÅGON valfri etikett. */
static void parse_plan_label(const cJSON *root, bool trust_strings,
                             tk_mt_provider *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, "planLabel");
  if (!item) return; /* frånvarande fält -> frånvarande flagga, giltigt */
  if (!trust_strings) return;
  if (!cJSON_IsString(item) || !item->valuestring) return;
  size_t length = strlen(item->valuestring);
  if (length < 1 || length > sizeof out->plan_label - 1) return;
  for (size_t i = 0; i < length; i++) {
    if (!plan_label_char_allowed((unsigned char)item->valuestring[i])) return;
  }
  memcpy(out->plan_label, item->valuestring, length + 1);
  out->has_plan = true;
}

/* En generisk dubblett-vakt: cJSON accepterar tyst dubblerade nycklar
 * (senaste vinner) — det är en mångtydighet en fientlig payload kan utnyttja
 * för att smuggla ett skuggat värde förbi granskningen. Samma regel som
 * has_duplicate_known_top_level_key i tokens-parsern, generaliserad till en
 * godtycklig nyckellista så den kan återanvändas för både toppnivån och
 * varje provider-objekt. */
static bool has_duplicate_key(const cJSON *node, const char *const *keys,
                              size_t count) {
  if (!cJSON_IsObject(node)) return false;
  for (const cJSON *item = node->child; item; item = item->next) {
    if (!item->string) continue;
    bool known = false;
    for (size_t i = 0; i < count; i++) {
      if (strcmp(item->string, keys[i]) == 0) {
        known = true;
        break;
      }
    }
    if (!known) continue;
    for (const cJSON *prior = node->child; prior != item; prior = prior->next) {
      if (prior->string && strcmp(prior->string, item->string) == 0) {
        return true;
      }
    }
  }
  return false;
}

static const char *const top_level_keys[] = {
    "v", "weeks", "stale", "codingStreakDays", "claude", "codex",
};

static const char *const provider_keys[] = {
    "avgPeakPct", "maxWeeksStreak", "maxWeeks",
    "maxDays",    "planLabel",      "weekMaxed",
    "days",
};

/* cJSON representerar ett avkodat NUL-tecken som C-strängens terminator. Par av
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

static bool parse_provider(const cJSON *node, bool trust_strings,
                           tk_mt_provider *out) {
  if (!cJSON_IsObject(node)) return false;
  if (has_duplicate_key(node, provider_keys,
                        sizeof provider_keys / sizeof provider_keys[0])) {
    return false;
  }

  tk_mt_provider p = {0};
  if (!avg_peak_pct(node, &p.avg_peak_pct, &p.has_avg)) return false;
  if (!bounded_int(node, "maxWeeksStreak", 0, TK_MT_WEEKS,
                   &p.max_weeks_streak)) {
    return false;
  }
  if (!bounded_int(node, "maxWeeks", 0, TK_MT_WEEKS, &p.max_weeks)) {
    return false;
  }
  if (!bounded_int(node, "maxDays", 0, TK_MT_DAYS, &p.max_days)) return false;
  if (!parse_week_maxed(node, p.week_maxed)) return false;
  if (!parse_days(node, p.days)) return false;
  parse_plan_label(node, trust_strings, &p);

  *out = p;
  return true;
}

bool tk_max_tracker_parse(const char *json, size_t len, tk_max_tracker *out) {
  if (!json || !out) return false;
  if (memchr(json, '\0', len)) return false;
  cJSON *root = cJSON_ParseWithLength(json, len);
  if (!root) return false;

  bool ok = false;
  tk_max_tracker t = {0};
  double v = 0, weeks = 0;
  raw_json_string_scan strings = {0};
  if (!scan_raw_json_strings(json, len, &strings) || strings.nul_key ||
      has_duplicate_key(root, top_level_keys,
                        sizeof top_level_keys / sizeof top_level_keys[0])) {
    goto done;
  }
  bool trust_strings = !strings.nul_string_value;

  /* Tjänstens felform ({"error": "..."}) parsar fint som JSON — avvisa den
   * per kontrakt, inte av misstag. */
  if (cJSON_GetObjectItemCaseSensitive(root, "error")) goto done;

  if (!num(root, "v", &v) || v != 1.0) goto done;
  if (!num(root, "weeks", &weeks) || weeks != (double)TK_MT_WEEKS) goto done;

  const cJSON *stale = cJSON_GetObjectItemCaseSensitive(root, "stale");
  if (!cJSON_IsBool(stale)) goto done;
  t.stale = cJSON_IsTrue(stale);

  if (!coding_streak(root, "codingStreakDays", &t.coding_streak_days)) {
    goto done;
  }

  const cJSON *claude = cJSON_GetObjectItemCaseSensitive(root, "claude");
  const cJSON *codex = cJSON_GetObjectItemCaseSensitive(root, "codex");
  if (!parse_provider(claude, trust_strings, &t.claude)) goto done;
  if (!parse_provider(codex, trust_strings, &t.codex)) goto done;

  *out = t;
  ok = true;

done:
  cJSON_Delete(root);
  return ok;
}
