#include <math.h>
#include <stdio.h>

#include "../components/app_tokens/agent_usage.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tk_limit limit(double pct) {
  tk_limit value = {0};
  value.has_pct = 1;
  value.pct = pct;
  return value;
}

int main(void) {
  tk_limit session = limit(21.0);
  tk_limit week = limit(49.0);
  tk_limit fable = limit(73.0);
  tk_agent_usage usage = tk_agent_usage_pick(&session, &week, &fable);
  check("högsta giltiga fönster är Fable",
        usage.has_pct && usage.pct == 73.0 &&
            usage.window == TK_USAGE_FABLE);

  session = limit(95.0);
  usage = tk_agent_usage_pick(&session, &week, &fable);
  check("sessionen kan vinna",
        usage.has_pct && usage.pct == 95.0 &&
            usage.window == TK_USAGE_SESSION);

  fable.has_pct = 0;
  usage = tk_agent_usage_pick(&session, &week, &fable);
  check("saknad Fable ignoreras",
        usage.has_pct && usage.pct == 95.0 &&
            usage.window == TK_USAGE_SESSION);

  session = limit(50.0);
  week = limit(50.0);
  fable = limit(50.0);
  usage = tk_agent_usage_pick(&session, &week, &fable);
  check("Fable vinner trevägslika",
        usage.has_pct && usage.pct == 50.0 &&
            usage.window == TK_USAGE_FABLE);

  fable.has_pct = 0;
  usage = tk_agent_usage_pick(&session, &week, &fable);
  check("veckan vinner lika utan Fable",
        usage.has_pct && usage.pct == 50.0 &&
            usage.window == TK_USAGE_WEEK);

  session.pct = NAN;
  week.pct = INFINITY;
  fable.has_pct = 1;
  fable.pct = -INFINITY;
  usage = tk_agent_usage_pick(&session, &week, &fable);
  check("icke-ändliga procentsatser ignoreras",
        !usage.has_pct && usage.window == TK_USAGE_NONE);

  usage = tk_agent_usage_pick(NULL, NULL, NULL);
  check("saknade fönster ger ärligt tomt resultat",
        !usage.has_pct && usage.window == TK_USAGE_NONE);

  if (failures == 0) {
    printf("OK: alla agent-usage-tester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
