#ifndef AGENT_STATUS_PARSE_H
#define AGENT_STATUS_PARSE_H

#include <stdbool.h>
#include <stddef.h>

#include "agent_status.h"

bool tk_agent_status_parse(const char *json, size_t len,
                           tk_agent_snapshot *out);

#endif
