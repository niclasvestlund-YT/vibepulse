#ifndef AGENT_STATUS_SOURCE_POLICY_H
#define AGENT_STATUS_SOURCE_POLICY_H

#include <stdbool.h>
#include <stdint.h>

#define TK_AGENT_LAN_FRESH_MS 5000u
#define TK_AGENT_RELAY_STALE_MS 30000u

typedef struct {
  uint64_t last_lan_ms;
  uint64_t last_relay_ms;
  bool has_lan;
  bool relay_owns_rows;
  bool relay_clear_consumed;
} tk_agent_source_policy;

void tk_agent_source_policy_init(tk_agent_source_policy *policy);
void tk_agent_source_note_lan(tk_agent_source_policy *policy,
                              uint64_t now_ms);
bool tk_agent_source_allow_relay(const tk_agent_source_policy *policy,
                                 uint64_t now_ms);
void tk_agent_source_note_relay(tk_agent_source_policy *policy,
                                uint64_t now_ms);
/* Returns true once for each relay-owned generation that reaches the stale
 * boundary. The successful result consumes the clear so a missing mailbox
 * cannot repaint an empty snapshot every poll. */
bool tk_agent_source_should_clear_relay(tk_agent_source_policy *policy,
                                        uint64_t now_ms);

#endif
