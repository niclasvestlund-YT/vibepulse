#include "agent_monitor_policy.h"

static int state_priority(tk_agent_state state) {
  switch (state) {
    case TK_AGENT_WAITING: return 5;
    case TK_AGENT_ERROR: return 4;
    case TK_AGENT_WORKING: return 3;
    case TK_AGENT_DONE: return 2;
    case TK_AGENT_IDLE: return 1;
    case TK_AGENT_UNKNOWN: return 0;
  }
  return 0;
}

static int best_provider(const tk_agent_status agents[2],
                         const bool present[2]) {
  int best = -1;
  for (int provider = 0; provider < 2; provider++) {
    if (!present[provider]) continue;
    if (best < 0 ||
        state_priority(agents[provider].state) >
            state_priority(agents[best].state) ||
        (state_priority(agents[provider].state) ==
             state_priority(agents[best].state) &&
         agents[provider].updated_ms < agents[best].updated_ms)) {
      best = provider;
    }
  }
  return best;
}

int tk_agent_monitor_resolve_provider(const tk_agent_status agents[2],
                                      const bool present[2], int selected,
                                      bool manual_choice) {
  int best = best_provider(agents, present);
  if (!manual_choice || selected < 0 || selected > 1 || !present[selected]) {
    return best;
  }
  if (best < 0 || state_priority(agents[best].state) <=
                      state_priority(agents[selected].state)) {
    return selected;
  }
  return best;
}
