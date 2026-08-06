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

int main(void) {
  size_t len;
  char *json;
  tk_tokens t = {0};

  json = read_file(FIXTURES_DIR "/tokens.json", &len);
  if (json) {
    check("fixturen parsar", tk_tokens_parse(json, len, &t));
    check("dayTokens", t.day_tokens == 48231907.0);
    check("takt", t.day_tokens_per_hour == 5120000.0);
    check("sessioner", t.day_sessions == 4);
    check("månaden", t.month_tokens == 612480233.0);
    free(json);
  }

  /* En vilande dag är giltig: nollor är ärliga när inget brunnit. */
  check("nollor ok",
        PARSE("{\"v\":1,\"dayTokens\":0,\"dayTokensPerHour\":0,"
              "\"daySessions\":0,\"monthTokens\":0}", &t)
        && t.day_tokens == 0);

  /* Fientliga indata: allt som inte är hela kontraktet avvisas utan att
   * röra utdata. */
  tk_tokens before = t;
  check("error-formen avvisas",
        !PARSE("{\"error\":\"scan failed\"}", &t));
  check("fel version avvisas",
        !PARSE("{\"v\":2,\"dayTokens\":1,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1}", &t));
  check("saknat fält avvisas",
        !PARSE("{\"v\":1,\"dayTokens\":1}", &t));
  check("negativt avvisas",
        !PARSE("{\"v\":1,\"dayTokens\":-5,\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1}", &t));
  check("sträng i talfält avvisas",
        !PARSE("{\"v\":1,\"dayTokens\":\"48\",\"dayTokensPerHour\":0,"
               "\"daySessions\":1,\"monthTokens\":1}", &t));
  check("trunkerad avvisas", !PARSE("{\"v\":1,\"dayTok", &t));
  check("html avvisas", !PARSE("<html>502</html>", &t));
  check("avvisning rör inte utdata", memcmp(&before, &t, sizeof t) == 0);

  if (failures == 0) { printf("OK: alla tokens-tester gröna\n"); return 0; }
  printf("%d test föll\n", failures);
  return 1;
}
