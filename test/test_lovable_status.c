/* Strict /api/lovable parsing: numbers only, unknown stays unknown. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../components/app_tokens/lovable_status_parse.h"

static bool parse(const char *json, tk_lovable_status *out) {
  return tk_lovable_status_parse(json, strlen(json), out);
}

int main(void) {
  tk_lovable_status s;
  assert(parse("{\"v\":1,\"enabled\":false}", &s) && !s.enabled);
  assert(!parse("{\"v\":1,\"enabled\":false,\"stale\":true}", &s));

  assert(parse("{\"v\":1,\"enabled\":true,\"stale\":false,"
               "\"creditsTenths\":12405,\"ageSeconds\":90,"
               "\"plan\":\"PRO\",\"workspace\":\"Niclas studio\"}", &s));
  assert(s.enabled && !s.stale && s.has_data && s.has_plan && s.has_workspace);
  assert(s.credits_tenths == 12405 && s.age_seconds == 90);
  assert(strcmp(s.plan, "PRO") == 0 && strcmp(s.workspace, "Niclas studio") == 0);

  /* Waiting and sign-in states carry no numbers. */
  assert(parse("{\"v\":1,\"enabled\":true,\"stale\":true}", &s));
  assert(!s.has_data && !s.needs_login);
  assert(parse("{\"v\":1,\"enabled\":true,\"stale\":true,\"login\":true}", &s));
  assert(s.needs_login && !s.has_data);
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":true,\"login\":false}", &s));

  /* Plan/workspace without a balance, half a pair, and invented fields. */
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"plan\":\"PRO\"}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":1}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":1,"
                "\"ageSeconds\":1,\"percent\":50}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":1,"
                "\"ageSeconds\":1,\"resetsAt\":1}", &s));
  /* Grant, reset and period: only with a balance, positive, consistent. */
  assert(parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":815,"
               "\"ageSeconds\":5,\"grantTenths\":1000,\"resetSeconds\":5270400,"
               "\"periodSeconds\":7862400}", &s));
  assert(s.has_grant && s.grant_tenths == 1000 && s.has_reset &&
         s.reset_seconds == 5270400 && s.has_period);
  assert(parse("{\"v\":1,\"enabled\":true,\"stale\":false,"
               "\"creditsTenths\":815,\"ageSeconds\":5,"
               "\"dailyCreditsTenths\":30,\"dailyGrantTenths\":50}", &s));
  assert(s.has_daily_credits && s.daily_credits_tenths == 30 &&
         s.has_daily_grant && s.daily_grant_tenths == 50);
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,"
                "\"creditsTenths\":8,\"ageSeconds\":5,"
                "\"dailyGrantTenths\":0}", &s));
  assert(parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":8,"
               "\"ageSeconds\":5}", &s) && !s.has_grant && !s.has_reset);
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"grantTenths\":10}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":8,"
                "\"ageSeconds\":5,\"grantTenths\":0}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":8,"
                "\"ageSeconds\":5,\"periodSeconds\":100}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":8,"
                "\"ageSeconds\":5,\"resetSeconds\":100,\"periodSeconds\":50}", &s));
  /* Negative, fractional, lowercase plan, non-ASCII, duplicates, trailing. */
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":-1,"
                "\"ageSeconds\":1}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":1.5,"
                "\"ageSeconds\":1}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":1,"
                "\"ageSeconds\":1,\"plan\":\"pro\"}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"creditsTenths\":1,"
                "\"ageSeconds\":1,\"workspace\":\"V\xc3\xa4rdera\"}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"enabled\":true,\"stale\":false}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false} x", &s));
  assert(!parse("{\"v\":2,\"enabled\":true,\"stale\":false}", &s));
  assert(!parse("{\"v\":1,\"enabled\":true,\"stale\":false,\"workspace\":\"a\\u0000b\","
                "\"creditsTenths\":1,\"ageSeconds\":1}", &s));
  puts("OK: Lovable payload is strict, numbers-only and never invents fields");
  return 0;
}
