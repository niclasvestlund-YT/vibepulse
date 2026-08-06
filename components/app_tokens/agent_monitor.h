#ifndef AGENT_MONITOR_H
#define AGENT_MONITOR_H

#include <stdint.h>

#include "lvgl.h"

#include "agent_status.h"
#include "agent_usage.h"

void tk_agent_monitor_create(lv_obj_t *root);
void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot, int64_t now_us);
void tk_agent_monitor_tick(int64_t now_us);
void tk_agent_monitor_set_usage(bool claude, tk_agent_usage usage);

#endif
