/*
 * Parser tests against the ACTUAL fixture files in sim-fixtures/ — the same
 * bytes the simulator cycles and the closest thing to prod payloads we own.
 * A fixture that drifts from the parser fails here, on the Mac, not on a
 * shelf in the living room.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../components/app_solelkollen/glance_parse.h"

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

int main(void) {
  size_t len;
  char *json;
  sg_glance g;
  char day[11];

  /* midnight: captured on prod after the midnight fix — the honest zero. */
  json = read_file(FIXTURES_DIR "/midnight.json", &len);
  if (json) {
    check("midnight parsar", sg_glance_parse(json, len, &g, day, sizeof day));
    check("midnight kr == 0", g.kr == 0);
    check("midnight dag", strcmp(day, "2026-07-17") == 0);
    check("midnight totalKr", g.total_kr == 103147);
    check("midnight level dyr", g.level == 1);
    free(json);
  }

  /* evening: the 100,6 kr day that burst the first layout. */
  json = read_file(FIXTURES_DIR "/evening.json", &len);
  if (json) {
    check("evening parsar", sg_glance_parse(json, len, &g, day, sizeof day));
    check("evening kr", g.kr > 100.59 && g.kr < 100.61);
    check("evening payback", g.payback_pct > 60.5 && g.payback_pct < 60.7);
    free(json);
  }

  /* midday: synthetic, the only one with a live rate. */
  json = read_file(FIXTURES_DIR "/midday.json", &len);
  if (json) {
    check("midday parsar", sg_glance_parse(json, len, &g, NULL, 0));
    check("midday rate", g.kr_per_hour > 10.39 && g.kr_per_hour < 10.41);
    check("midday w", g.w == 8931);
    free(json);
  }

  /* error-502: valid JSON, must still be rejected by contract. */
  json = read_file(FIXTURES_DIR "/error-502.json", &len);
  if (json) {
    sg_glance before = g;
    check("error avvisas", !sg_glance_parse(json, len, &g, day, sizeof day));
    check("error rör inte utdata", memcmp(&before, &g, sizeof g) == 0);
    free(json);
  }

  /* --- Sverige-parsern (P23) --------------------------------------------- */
  {
    sg_sverige s = {0};
    /* Det riktiga svaret, fångat mot dev-servern 2026-08-05. */
    json = read_file(FIXTURES_DIR "/sverige.json", &len);
    if (json) {
      check("sverige fixtur", sg_sverige_parse(json, len, &s));
      check("sverige gwh", s.day_gwh > 17.3 && s.day_gwh < 17.5);
      check("sverige dag", strcmp(s.day, "2026-08-04") == 0);
      check("sverige andel", s.has_share == 1 && s.share_pct > 5.0 && s.share_pct < 5.2);
      free(json);
    }

    /* null-andel är giltig och skiljer sig från saknad. */
    check("sverige null-andel ok",
          sg_sverige_parse("{\"v\":1,\"day\":\"2026-08-04\",\"dayGwh\":17.4,"
                           "\"sharePct\":null,\"yearGwh\":null}", 71, &s)
          && s.has_share == 0);
    sg_sverige before_s = s;
    check("sverige utan sharePct avvisas",
          !sg_sverige_parse("{\"v\":1,\"day\":\"2026-08-04\",\"dayGwh\":17.4}", 41, &s));
    check("sverige error avvisas",
          !sg_sverige_parse("{\"error\":\"eSett: no settled day\"}", 33, &s));
    check("sverige fel dag avvisas",
          !sg_sverige_parse("{\"v\":1,\"day\":\"4 aug\",\"dayGwh\":17.4,\"sharePct\":5.1}", 50, &s));
    check("sverige avvisning rör inte utdata", memcmp(&before_s, &s, sizeof s) == 0);
  }

  /* Hostile inputs: truncated, wrong version, not JSON at all. */
  check("trunkerad avvisas", !sg_glance_parse("{\"v\":1,\"kr\":", 12, &g, NULL, 0));
  check("fel version avvisas",
        !sg_glance_parse("{\"v\":2,\"day\":\"2026-07-17\",\"kr\":1,\"krPerHour\":0,"
                         "\"w\":0,\"ore\":1,\"level\":0,\"yearKr\":1,\"totalKr\":1,"
                         "\"installationKr\":1,\"paybackPct\":1}", 130, &g, NULL, 0));
  check("html avvisas", !sg_glance_parse("<html>502</html>", 16, &g, NULL, 0));

  if (failures == 0) { printf("OK: alla parser-tester gröna\n"); return 0; }
  printf("%d test föll\n", failures);
  return 1;
}
