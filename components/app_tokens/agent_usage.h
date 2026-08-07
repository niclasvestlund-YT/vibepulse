#ifndef AGENT_USAGE_H
#define AGENT_USAGE_H

#include <stdbool.h>

#include "tokens.h"

typedef enum {
  TK_USAGE_NONE,
  TK_USAGE_SESSION,
  TK_USAGE_WEEK,
  TK_USAGE_FABLE,
} tk_usage_window;

typedef struct {
  bool has_pct;
  double pct;
  tk_usage_window window;
} tk_agent_usage;

tk_agent_usage tk_agent_usage_pick(const tk_limit *session,
                                   const tk_limit *week,
                                   const tk_limit *fable);

#endif
