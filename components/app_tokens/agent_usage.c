#include "agent_usage.h"

#include <math.h>

static void consider(tk_agent_usage *best, const tk_limit *candidate,
                     tk_usage_window window) {
  if (!candidate || !candidate->has_pct || !isfinite(candidate->pct)) return;

  /* Kandidaterna matas i stigande tie-prioritet. >= gör därför att
   * Fable vinner över veckan, som vinner över sessionen vid lika värde. */
  if (!best->has_pct || candidate->pct >= best->pct) {
    best->has_pct = true;
    best->pct = candidate->pct;
    best->window = window;
  }
}

tk_agent_usage tk_agent_usage_pick(const tk_limit *session,
                                   const tk_limit *week,
                                   const tk_limit *fable) {
  tk_agent_usage best = {0};
  best.window = TK_USAGE_NONE;
  consider(&best, session, TK_USAGE_SESSION);
  consider(&best, week, TK_USAGE_WEEK);
  consider(&best, fable, TK_USAGE_FABLE);
  return best;
}
