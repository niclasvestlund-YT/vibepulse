#ifndef AGENT_MONITOR_POLICY_H
#define AGENT_MONITOR_POLICY_H

#include <stdbool.h>
#include <stddef.h>
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
uint8_t tk_agent_monitor_presence_mask(
    const tk_agent_status agents[2],
    const char dismissed_event_ids[2][TK_AGENT_ID_CAP],
    uint64_t packet_age_ms);
bool tk_agent_monitor_tick_should_render(bool has_snapshot, int selected,
                                         uint8_t rendered_presence_mask,
                                         uint8_t current_presence_mask);

/* Display-safe project alphabet: ASCII A-Z, 0-9, space, '-', '.', '_',
 * plus Swedish ÅÄÖ. ASCII and Swedish lowercase are uppercased; every other
 * valid UTF-8 code point becomes '?'. Output is always NUL-terminated when
 * capacity is nonzero and never ends with a partial UTF-8 sequence. */
void tk_agent_monitor_project_label(const char *source, char *destination,
                                    size_t capacity);

void tk_agent_monitor_manual_choice_set(tk_agent_manual_choice *choice,
                                        int provider,
                                        const tk_agent_status agents[2]);
void tk_agent_monitor_manual_choice_clear(tk_agent_manual_choice *choice);

int tk_agent_monitor_resolve_provider(const tk_agent_status agents[2],
                                      const bool present[2],
                                      const tk_agent_manual_choice *choice);

/* Säker, begränsad aktivitetstext för den inbäddade nederkanten. Inga
 * promptar, kommandon eller filnamn passerar denna mapping. */
const char *tk_agent_monitor_activity_text(const tk_agent_status *status);

#endif
