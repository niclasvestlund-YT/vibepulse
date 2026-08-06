#include "agent_monitor_policy.h"

#include <string.h>

tk_agent_state tk_agent_monitor_effective_state(
    const tk_agent_status *status, uint64_t packet_age_ms) {
  if (!status) return TK_AGENT_UNKNOWN;
  if (status->state != TK_AGENT_WORKING) return status->state;
  if ((uint64_t)status->updated_ms > TK_AGENT_WORKING_LEASE_MS ||
      packet_age_ms >
          TK_AGENT_WORKING_LEASE_MS - (uint64_t)status->updated_ms) {
    return TK_AGENT_UNKNOWN;
  }
  return TK_AGENT_WORKING;
}

bool tk_agent_monitor_status_present(const tk_agent_status *status,
                                     const char *dismissed_event_id,
                                     uint64_t packet_age_ms) {
  tk_agent_state state =
      tk_agent_monitor_effective_state(status, packet_age_ms);
  if (state == TK_AGENT_IDLE || state == TK_AGENT_UNKNOWN) return false;
  return !dismissed_event_id || !status->event_id[0] ||
         strcmp(status->event_id, dismissed_event_id) != 0;
}

bool tk_agent_monitor_should_keep_awake(const tk_agent_status *status,
                                        const char *dismissed_event_id,
                                        uint64_t packet_age_ms) {
  return tk_agent_monitor_effective_state(status, packet_age_ms) ==
             TK_AGENT_WORKING &&
         tk_agent_monitor_status_present(status, dismissed_event_id,
                                         packet_age_ms);
}

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

void tk_agent_monitor_manual_choice_set(tk_agent_manual_choice *choice,
                                        int provider,
                                        const tk_agent_status agents[2]) {
  if (!choice) return;
  memset(choice, 0, sizeof *choice);
  if (!agents || provider < 0 || provider > 1) return;
  choice->active = true;
  choice->provider = provider;
  for (int i = 0; i < 2; i++) {
    memcpy(choice->seen_event_id[i], agents[i].event_id,
           sizeof choice->seen_event_id[i]);
    choice->seen_state[i] = agents[i].state;
  }
}

void tk_agent_monitor_manual_choice_clear(tk_agent_manual_choice *choice) {
  if (choice) memset(choice, 0, sizeof *choice);
}

static bool generation_changed(const tk_agent_manual_choice *choice,
                               int provider,
                               const tk_agent_status agents[2]) {
  return choice->seen_state[provider] != agents[provider].state ||
         strcmp(choice->seen_event_id[provider],
                agents[provider].event_id) != 0;
}

int tk_agent_monitor_resolve_provider(const tk_agent_status agents[2],
                                      const bool present[2],
                                      const tk_agent_manual_choice *choice) {
  int best = best_provider(agents, present);
  if (!choice || !choice->active || choice->provider < 0 ||
      choice->provider > 1 || !present[choice->provider]) {
    return best;
  }
  int selected = choice->provider;
  if (best < 0 || state_priority(agents[best].state) <=
                      state_priority(agents[selected].state)) {
    return selected;
  }
  /* Ett redan synligt waiting/error-event ska inte rycka tillbaka vyn på
   * varje identisk repoll. Bara en ny högre eventgeneration efter trycket
   * får bryta användarens uttryckliga provider-val. */
  return generation_changed(choice, best, agents) ? best : selected;
}
