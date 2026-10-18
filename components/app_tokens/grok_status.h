#ifndef GROK_STATUS_H
#define GROK_STATUS_H
#include <stdbool.h>
#include <stdint.h>
typedef struct { char name[17], state[8], activity[17]; } tk_grok_bot;
typedef struct {
  bool enabled, stale;
  char source[11], label[17];
  bool has_age, has_spent, has_limit, has_prepaid, has_used, has_reset;
  int32_t age_seconds, spent_cents, limit_cents, prepaid_cents, used_pct, reset_seconds;
  int32_t active, done_today;
  unsigned bot_count;
  tk_grok_bot bots[4];
} tk_grok_status;
#endif
