/*
 * Max Tracker-parserns tester — samma metod som test_tokens.c: de riktiga
 * fixturerna (samma bytes simulatorn cyklar) plus fientlig indata. En
 * fixtur som glider ifrån parsern faller här, på Macen, inte på hyllan.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../components/app_tokens/max_tracker_parse.h"

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

/* Literaler parsas med sin faktiska längd — en felräknad hårdkodad längd är
 * ett test av testet, inte av parsern. */
#define PARSE(s, out) tk_max_tracker_parse((s), strlen(s), (out))

/* Varje avvisat fall sås med ett bytemönster innan anropet — en handske som
 * en råkad noll-kollision inte kan smita förbi — och jämförs sedan bakåt
 * mot exakt samma mönster. Om parsern rört *out alls syns det här. */
static void seed_pattern(tk_max_tracker *out) {
  memset(out, 0xA5, sizeof *out);
}

static void check_rejected_untouched(const char *what, const char *json,
                                     tk_max_tracker *out) {
  seed_pattern(out);
  tk_max_tracker before;
  memcpy(&before, out, sizeof before);
  char preserved[160];
  check(what, !PARSE(json, out));
  snprintf(preserved, sizeof preserved, "%s preserves output", what);
  check(preserved, memcmp(&before, out, sizeof before) == 0);
}

static void check_rejected_bytes_untouched(const char *what, const char *json,
                                           size_t length, tk_max_tracker *out) {
  seed_pattern(out);
  tk_max_tracker before;
  memcpy(&before, out, sizeof before);
  char preserved[160];
  check(what, !tk_max_tracker_parse(json, length, out));
  snprintf(preserved, sizeof preserved, "%s preserves output", what);
  check(preserved, memcmp(&before, out, sizeof before) == 0);
}

/* 20 nollor — en giltig weekMaxed-array när inget testar just den. */
#define WEEK_MAXED_20 "[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]"

/* Bygger en "days"-array med `count` poster, alla [0,0] utom `bad_index`
 * (om >= 0) som får den råa literalen `bad_literal` istället. */
static void build_days_array(char *buf, size_t cap, int count, int bad_index,
                             const char *bad_literal) {
  size_t offset = 0;
  offset += (size_t)snprintf(buf + offset, cap - offset, "[");
  for (int i = 0; i < count; i++) {
    const char *entry = (i == bad_index) ? bad_literal : "[0,0]";
    offset += (size_t)snprintf(buf + offset, cap - offset, "%s%s",
                               i ? "," : "", entry);
  }
  snprintf(buf + offset, cap - offset, "]");
}

/* Bygger en fullt giltig provider-JSON runt en färdig days-array, med
 * eventuella extra fält (t.ex. planLabel) inklistrade före sista klammern. */
static void build_full_provider(char *buf, size_t cap, const char *days_json,
                                const char *extra) {
  snprintf(buf, cap,
          "{\"avgPeakPct\":null,\"maxWeeksStreak\":0,\"maxWeeks\":0,"
          "\"maxDays\":0,\"weekMaxed\":" WEEK_MAXED_20 ",\"days\":%s%s}",
          days_json, extra ? extra : "");
}

static void build_doc(char *buf, size_t cap, const char *v, const char *weeks,
                      const char *stale, const char *streak,
                      const char *claude_json, const char *codex_json) {
  snprintf(buf, cap,
          "{\"v\":%s,\"weeks\":%s,\"stale\":%s,\"codingStreakDays\":%s,"
          "\"claude\":%s,\"codex\":%s}",
          v, weeks, stale, streak, claude_json, codex_json);
}

int main(void) {
  size_t len;
  char *json;
  tk_max_tracker t;
  memset(&t, 0, sizeof t);

  /* --- Fixturer: de riktiga bytesen simulatorn renderar --- */

  json = read_file(FIXTURES_DIR "/max-tracker-full.json", &len);
  if (json) {
    check("full: fixturen parsar", tk_max_tracker_parse(json, len, &t));
    check("full: inte stale", !t.stale);
    check("full: coding streak", t.coding_streak_days == 47);

    check("full: claude avg", t.claude.has_avg && t.claude.avg_peak_pct == 84.0);
    check("full: claude max weeks streak", t.claude.max_weeks_streak == 6);
    check("full: claude max weeks", t.claude.max_weeks == 6);
    check("full: claude max days", t.claude.max_days == 12);
    check("full: claude ingen planLabel", !t.claude.has_plan);
    check("full: claude weekMaxed[13] av", !t.claude.week_maxed[13]);
    check("full: claude weekMaxed[14] på", t.claude.week_maxed[14]);
    check("full: claude weekMaxed[19] på", t.claude.week_maxed[19]);
    check("full: claude days[0]", t.claude.days[0].pct == 75 && t.claude.days[0].lvl == 2);
    check("full: claude days[13]", t.claude.days[13].pct == 39 && t.claude.days[13].lvl == 1);
    check("full: claude days[14]", t.claude.days[14].pct == 36 && t.claude.days[14].lvl == 1);
    check("full: claude days[135]", t.claude.days[135].pct == 39 && t.claude.days[135].lvl == 1);
    check("full: claude days[139]", t.claude.days[139].pct == 100 && t.claude.days[139].lvl == 2);

    check("full: codex planLabel",
          t.codex.has_plan && strcmp(t.codex.plan_label, "PRO") == 0);
    check("full: codex avg", t.codex.has_avg && t.codex.avg_peak_pct == 84.0);
    check("full: codex max weeks streak", t.codex.max_weeks_streak == 6);
    check("full: codex weekMaxed[14] på", t.codex.week_maxed[14]);
    check("full: codex days[139]", t.codex.days[139].pct == 100 && t.codex.days[139].lvl == 2);
    free(json);
  }

  json = read_file(FIXTURES_DIR "/max-tracker-coldstart.json", &len);
  if (json) {
    check("coldstart: fixturen parsar", tk_max_tracker_parse(json, len, &t));
    check("coldstart: coding streak", t.coding_streak_days == 9);
    check("coldstart: claude planLabel",
          t.claude.has_plan && strcmp(t.claude.plan_label, "MAX 20X") == 0);
    check("coldstart: claude avg", t.claude.has_avg && t.claude.avg_peak_pct == 62.0);
    check("coldstart: claude days[0] tom",
          t.claude.days[0].pct == -1 && t.claude.days[0].lvl == -1);
    check("coldstart: claude days[13] tom",
          t.claude.days[13].pct == -1 && t.claude.days[13].lvl == -1);
    check("coldstart: claude days[14] gråskala",
          t.claude.days[14].pct == -1 && t.claude.days[14].lvl == 0);
    check("coldstart: claude days[135] färgad",
          t.claude.days[135].pct == 55 && t.claude.days[135].lvl == 2);
    check("coldstart: claude days[139] färgad",
          t.claude.days[139].pct == 91 && t.claude.days[139].lvl == 2);
    check("coldstart: codex planLabel",
          t.codex.has_plan && strcmp(t.codex.plan_label, "PRO") == 0);
    free(json);
  }

  json = read_file(FIXTURES_DIR "/max-tracker-empty.json", &len);
  if (json) {
    check("empty: fixturen parsar", tk_max_tracker_parse(json, len, &t));
    check("empty: okänd streak", t.coding_streak_days == -1);
    check("empty: claude ingen avg", !t.claude.has_avg);
    check("empty: claude ingen plan", !t.claude.has_plan);
    check("empty: claude days[0] tom",
          t.claude.days[0].pct == -1 && t.claude.days[0].lvl == -1);
    check("empty: claude days[139] tom",
          t.claude.days[139].pct == -1 && t.claude.days[139].lvl == -1);
    check("empty: claude weekMaxed[19] av", !t.claude.week_maxed[19]);
    check("empty: codex ingen avg", !t.codex.has_avg);
    check("empty: codex ingen plan", !t.codex.has_plan);
    free(json);
  }

  /* --- En frisk minimal dokument-bas för positiva mutationstester --- */

  char claude_full[2048];
  char codex_full[2048];
  char full_days[2048];
  build_days_array(full_days, sizeof full_days, TK_MT_DAYS, -1, NULL);
  build_full_provider(claude_full, sizeof claude_full, full_days, NULL);
  build_full_provider(codex_full, sizeof codex_full, full_days, NULL);

  char doc[8192];
  build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_full, codex_full);
  check("frisk minimal doc parsar", PARSE(doc, &t));
  check("frisk: streak null -> -1", t.coding_streak_days == -1);
  check("frisk: claude ingen avg (null)", !t.claude.has_avg);
  check("frisk: claude ingen plan (saknas)", !t.claude.has_plan);

  build_doc(doc, sizeof doc, "1", "20", "true", "0", claude_full, codex_full);
  check("stale true parsar", PARSE(doc, &t) && t.stale);

  build_doc(doc, sizeof doc, "1", "20", "false", "0", claude_full, codex_full);
  check("streak 0 parsar", PARSE(doc, &t) && t.coding_streak_days == 0);

  /* --- planLabel: syntaktiskt ogiltig etikett tappas, resten parsar --- */

  char claude_plan[2048];
  const struct { const char *name, *label; bool valid; const char *expect; } plans[] = {
      {"exakt 11 tecken", "ABCDEFGHIJK", true, "ABCDEFGHIJK"},
      {"siffror och mellanslag", "MAX 20X", true, "MAX 20X"},
      {"12 tecken för långt", "ABCDEFGHIJKL", false, NULL},
      {"23 tecken för långt", "AAAAAAAAAAAAAAAAAAAAAAA", false, NULL},
      {"tom sträng", "", false, NULL},
      {"gemener", "pro", false, NULL},
      {"otillåtet tecken", "PR<O", false, NULL},
  };
  for (size_t i = 0; i < sizeof plans / sizeof plans[0]; i++) {
    char extra[64];
    snprintf(extra, sizeof extra, ",\"planLabel\":\"%s\"", plans[i].label);
    build_full_provider(claude_plan, sizeof claude_plan, full_days, extra);
    build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_plan, codex_full);
    char name[96];
    snprintf(name, sizeof name, "planLabel %s parsar dokumentet", plans[i].name);
    check(name, PARSE(doc, &t));
    snprintf(name, sizeof name, "planLabel %s har_plan", plans[i].name);
    check(name, t.claude.has_plan == plans[i].valid);
    if (plans[i].valid) {
      snprintf(name, sizeof name, "planLabel %s kopieras", plans[i].name);
      check(name, strcmp(t.claude.plan_label, plans[i].expect) == 0);
    }
    check("planLabel: resten av claude-provider parsar ändå",
          t.claude.max_days == 0 && !t.claude.has_avg);
    check("planLabel: codex parsar ändå oberoende", !t.codex.has_avg);
  }

  /* planLabel med fel typ (tal istället för sträng) tappas, resten parsar. */
  build_full_provider(claude_plan, sizeof claude_plan, full_days,
                      ",\"planLabel\":5");
  build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_plan, codex_full);
  check("planLabel fel typ parsar dokumentet", PARSE(doc, &t));
  check("planLabel fel typ har_plan false", !t.claude.has_plan);

  /* NUL-escape i en sträng gör att INGEN valfri etikett litas på, även om
   * den synliga (avkortade) texten själv skulle vara giltig — samma regel
   * som quota-etiketten i tokens-kontraktet. */
  build_full_provider(claude_plan, sizeof claude_plan, full_days,
                      ",\"planLabel\":\"PRO\\u0000BAD\"");
  build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_plan, codex_full);
  check("NUL-trunkerad planLabel parsar dokumentet", PARSE(doc, &t));
  check("NUL-trunkerad planLabel tappas", !t.claude.has_plan);

  /* --- Fientlig indata: allt som inte är hela kontraktet avvisas utan att
   * röra utdata. --- */

  check_rejected_untouched("fel version avvisas",
      "{\"v\":2,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("fraktionell version avvisas",
      "{\"v\":1.5,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("fel veckoantal avvisas",
      "{\"v\":1,\"weeks\":19,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("stale saknas avvisas",
      "{\"v\":1,\"weeks\":20,\"codingStreakDays\":null,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("stale fel typ avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":1,\"codingStreakDays\":null,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("negativ streak avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":-1,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("fraktionell streak avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":1.5,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("oändlig streak avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":1e999,"
      "\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("error-formen avvisas",
      "{\"error\":1}", &t);
  check_rejected_untouched("dubblerad toppnyckel avvisas",
      "{\"v\":1,\"v\":1,\"weeks\":20,\"stale\":false,"
      "\"codingStreakDays\":null,\"claude\":{},\"codex\":{}}", &t);
  check_rejected_untouched("saknad claude avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"codex\":{}}", &t);
  check_rejected_untouched("claude fel typ avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":[],\"codex\":{}}", &t);
  check_rejected_untouched("trunkerad avvisas", "{\"v\":1,\"wee", &t);
  check_rejected_untouched("html avvisas", "<html>502</html>", &t);

  static const char raw_nul_payload[] =
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"planLabel\":\"GOOD\0BAD\"},\"codex\":{}}";
  check_rejected_bytes_untouched("rå NUL-byte inom explicit längd avvisas",
      raw_nul_payload, sizeof raw_nul_payload - 1, &t);

  /* --- Provider-nivå: varje fält testas isolerat genom att de tidigare
   * fälten i valideringsordningen hålls giltiga. --- */

  check_rejected_untouched("claude avgPeakPct över 100 avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":101},\"codex\":{}}", &t);
  check_rejected_untouched("claude avgPeakPct negativ avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":-1},\"codex\":{}}", &t);
  check_rejected_untouched("claude maxWeeksStreak över 20 avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":null,\"maxWeeksStreak\":21},"
      "\"codex\":{}}", &t);
  check_rejected_untouched("claude maxWeeks över 20 avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":null,\"maxWeeksStreak\":0,"
      "\"maxWeeks\":21},\"codex\":{}}", &t);
  check_rejected_untouched("claude maxDays över 140 avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":null,\"maxWeeksStreak\":0,"
      "\"maxWeeks\":0,\"maxDays\":141},\"codex\":{}}", &t);
  check_rejected_untouched("claude weekMaxed fel längd (19) avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":null,\"maxWeeksStreak\":0,"
      "\"maxWeeks\":0,\"maxDays\":0,"
      "\"weekMaxed\":[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]},"
      "\"codex\":{}}", &t);
  check_rejected_untouched("claude weekMaxed fel längd (21) avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":null,\"maxWeeksStreak\":0,"
      "\"maxWeeks\":0,\"maxDays\":0,"
      "\"weekMaxed\":[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]},"
      "\"codex\":{}}", &t);
  check_rejected_untouched("claude weekMaxed värde 2 avvisas",
      "{\"v\":1,\"weeks\":20,\"stale\":false,\"codingStreakDays\":null,"
      "\"claude\":{\"avgPeakPct\":null,\"maxWeeksStreak\":0,"
      "\"maxWeeks\":0,\"maxDays\":0,"
      "\"weekMaxed\":[2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]},"
      "\"codex\":{}}", &t);

  char days_short[2048], days_long[2048];
  char claude_short[2560], claude_long[2560];
  build_days_array(days_short, sizeof days_short, TK_MT_DAYS - 1, -1, NULL);
  build_days_array(days_long, sizeof days_long, TK_MT_DAYS + 1, -1, NULL);
  build_full_provider(claude_short, sizeof claude_short, days_short, NULL);
  build_full_provider(claude_long, sizeof claude_long, days_long, NULL);
  build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_short, "{}");
  check_rejected_untouched("claude days fel längd (139) avvisas", doc, &t);
  build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_long, "{}");
  check_rejected_untouched("claude days fel längd (141) avvisas", doc, &t);

  char bad_days[2048], claude_bad_days[2560];
  const struct { const char *name, *literal; } bad_day_cases[] = {
      {"pct 101", "[101,0]"},
      {"lvl 3", "[0,3]"},
      {"pct sträng", "[\"x\",0]"},
      {"pct under -1", "[-2,0]"},
      {"lvl under -1", "[0,-2]"},
      {"fraktionell pct", "[1.5,0]"},
  };
  for (size_t i = 0; i < sizeof bad_day_cases / sizeof bad_day_cases[0]; i++) {
    build_days_array(bad_days, sizeof bad_days, TK_MT_DAYS, 70,
                     bad_day_cases[i].literal);
    build_full_provider(claude_bad_days, sizeof claude_bad_days, bad_days, NULL);
    build_doc(doc, sizeof doc, "1", "20", "false", "null",
             claude_bad_days, "{}");
    char name[64];
    snprintf(name, sizeof name, "claude days med %s avvisas",
            bad_day_cases[i].name);
    check_rejected_untouched(name, doc, &t);
  }

  /* --- Codex valideras också, inte bara claude --- */
  build_doc(doc, sizeof doc, "1", "20", "false", "null", claude_full,
           "{\"avgPeakPct\":101}");
  check_rejected_untouched("codex avgPeakPct över 100 avvisas", doc, &t);

  if (failures == 0) { printf("OK: alla max-tracker-tester gröna\n"); return 0; }
  printf("%d test föll\n", failures);
  return 1;
}
