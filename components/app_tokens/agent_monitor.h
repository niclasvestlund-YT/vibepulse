#ifndef AGENT_MONITOR_H
#define AGENT_MONITOR_H

#include <stdbool.h>
#include <stdint.h>

#include "lvgl.h"

#include "agent_status.h"
void tk_agent_monitor_create_footer(lv_obj_t *parent, bool claude);
void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot, int64_t now_us);
void tk_agent_monitor_tick(int64_t now_us);

#endif
