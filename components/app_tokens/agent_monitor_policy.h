#ifndef AGENT_MONITOR_POLICY_H
#define AGENT_MONITOR_POLICY_H

#include <stdbool.h>

#include "agent_status.h"

int tk_agent_monitor_resolve_provider(const tk_agent_status agents[2],
                                      const bool present[2], int selected,
                                      bool manual_choice);

#endif
