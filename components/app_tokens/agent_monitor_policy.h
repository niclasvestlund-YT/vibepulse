#ifndef AGENT_MONITOR_POLICY_H
#define AGENT_MONITOR_POLICY_H

#include <stdbool.h>
#include <stdint.h>

#include "agent_status.h"

typedef struct {
  bool active;
  int provider;
  char seen_event_id[2][TK_AGENT_ID_CAP];
  tk_agent_state seen_state[2];
} tk_agent_manual_choice;

#define TK_AGENT_WORKING_LEASE_MS 120000ULL

tk_agent_state tk_agent_monitor_effective_state(
    const tk_agent_status *status, uint64_t packet_age_ms);
bool tk_agent_monitor_status_present(const tk_agent_status *status,
                                     const char *dismissed_event_id,
                                     uint64_t packet_age_ms);
bool tk_agent_monitor_should_keep_awake(const tk_agent_status *status,
                                        const char *dismissed_event_id,
                                        uint64_t packet_age_ms);

void tk_agent_monitor_manual_choice_set(tk_agent_manual_choice *choice,
                                        int provider,
                                        const tk_agent_status agents[2]);
void tk_agent_monitor_manual_choice_clear(tk_agent_manual_choice *choice);

int tk_agent_monitor_resolve_provider(const tk_agent_status agents[2],
                                      const bool present[2],
                                      const tk_agent_manual_choice *choice);

#endif
