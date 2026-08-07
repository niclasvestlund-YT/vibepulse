#ifndef AGENT_MONITOR_H
#define AGENT_MONITOR_H

#include <stdint.h>

#include "lvgl.h"

#include "agent_status.h"

void tk_agent_monitor_create(lv_obj_t *app_root);
void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot, int64_t now_us);
void tk_agent_monitor_tick(int64_t now_us);

/* Deterministisk simulatorväg; glastrycket går genom samma köfunktion. */
void tk_agent_monitor_dismiss_current(void);

#endif
