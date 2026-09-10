#include <stdbool.h>
#include <stdio.h>

#include "../components/app_tokens/poll_backoff_policy.h"

static int failures;

static void check(const char *name, bool condition) {
  if (!condition) {
    fprintf(stderr, "FAIL: %s\n", name);
    ++failures;
  }
}

int main(void) {
  tk_poll_backoff b;
  tk_poll_backoff_init(&b, 1000, 30000);

  check("healthy poller keeps its cadence",
        tk_poll_backoff_delay_ms(&b) == 1000);
  check("one miss is normal: no change, no log",
        !tk_poll_backoff_note(&b, false) &&
            tk_poll_backoff_delay_ms(&b) == 1000);
  check("second consecutive miss doubles and is a transition",
        tk_poll_backoff_note(&b, false) &&
            tk_poll_backoff_delay_ms(&b) == 2000);
  check("third miss doubles again",
        tk_poll_backoff_note(&b, false) &&
            tk_poll_backoff_delay_ms(&b) == 4000);
  for (int i = 0; i < 10; ++i) (void)tk_poll_backoff_note(&b, false);
  check("the cap holds", tk_poll_backoff_delay_ms(&b) == 30000);
  check("at the cap further misses are not transitions",
        !tk_poll_backoff_note(&b, false) &&
            tk_poll_backoff_delay_ms(&b) == 30000);
  check("one success resets to the base and is a transition",
        tk_poll_backoff_note(&b, true) &&
            tk_poll_backoff_delay_ms(&b) == 1000);
  check("a success on a healthy poller is not a transition",
        !tk_poll_backoff_note(&b, true));

  /* The tokens poller: 30 s base, 300 s cap -- the ladder from OBS-13. */
  tk_poll_backoff t;
  tk_poll_backoff_init(&t, 30000, 300000);
  (void)tk_poll_backoff_note(&t, false);
  (void)tk_poll_backoff_note(&t, false);
  check("tokens: second miss 60 s", tk_poll_backoff_delay_ms(&t) == 60000);
  (void)tk_poll_backoff_note(&t, false);
  check("tokens: third miss 120 s", tk_poll_backoff_delay_ms(&t) == 120000);
  (void)tk_poll_backoff_note(&t, false);
  check("tokens: fourth miss 240 s", tk_poll_backoff_delay_ms(&t) == 240000);
  (void)tk_poll_backoff_note(&t, false);
  check("tokens: fifth miss caps at 300 s",
        tk_poll_backoff_delay_ms(&t) == 300000);

  /* Never wraps: a cap near the top of uint32 and a very long streak. */
  tk_poll_backoff w;
  tk_poll_backoff_init(&w, 3000000000u, 4000000000u);
  for (int i = 0; i < 40; ++i) (void)tk_poll_backoff_note(&w, false);
  check("no 32-bit wrap on a long streak",
        tk_poll_backoff_delay_ms(&w) == 4000000000u);

  /* Degenerate configuration is clamped, never zero. */
  tk_poll_backoff z;
  tk_poll_backoff_init(&z, 0, 0);
  check("zero base becomes one millisecond, cap follows",
        tk_poll_backoff_delay_ms(&z) == 1);
  tk_poll_backoff_init(&z, 5000, 100);
  check("a cap below the base is raised to the base",
        z.cap_ms == 5000);
  check("null is tolerated",
        tk_poll_backoff_delay_ms(NULL) == 0 &&
            !tk_poll_backoff_note(NULL, false));

  if (failures) return 1;
  puts("OK: poll backoff policy (first miss free, doubling to the cap, "
       "reset on success)");
  return 0;
}
