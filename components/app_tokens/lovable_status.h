#ifndef LOVABLE_STATUS_H
#define LOVABLE_STATUS_H

#include <stdbool.h>
#include <stdint.h>

#define TK_LOVABLE_PLAN_CAP 17
#define TK_LOVABLE_WORKSPACE_CAP 41

/* One strict /api/lovable payload. Numbers only: the OAuth login never
 * leaves the Mac. Unknown fields stay unknown (has_* false) and render as a
 * dash -- the panel never invents a plan, a balance, a grant or a reset. */
typedef struct {
  bool enabled;
  bool stale;
  bool needs_login;
  bool has_data;       /* credits_tenths + age_seconds are valid */
  bool has_plan;
  bool has_workspace;
  bool has_grant;      /* the source named a total credit grant */
  bool has_reset;      /* the source named a reset/expiry time */
  bool has_period;     /* ... and when that period started */
  bool has_daily_credits; /* the source named daily credits remaining */
  bool has_daily_grant;   /* the source named the daily allowance */
  int32_t credits_tenths;
  int32_t grant_tenths;
  int32_t reset_seconds;   /* from the Mac's snapshot moment */
  int32_t period_seconds;
  int32_t daily_credits_tenths;
  int32_t daily_grant_tenths;
  int32_t age_seconds;
  char plan[TK_LOVABLE_PLAN_CAP];
  char workspace[TK_LOVABLE_WORKSPACE_CAP];
} tk_lovable_status;

#endif
