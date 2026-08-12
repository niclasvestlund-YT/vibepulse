#ifndef USAGE_SCREEN_H
#define USAGE_SCREEN_H

#include <stdbool.h>
#include <stdint.h>

#include "lvgl.h"

#include "agent_status.h"
#include "max_tracker.h"
#include "tokens.h"

#define TK_USAGE_SCREEN_VIEWS 6

void usage_screen_create(lv_obj_t *root);
void usage_screen_apply_tokens(const tk_tokens *tokens);
void usage_screen_apply_agent(const tk_agent_snapshot *snapshot,
                              int64_t now_us);
void usage_screen_apply_max_tracker(const tk_max_tracker *t);
void usage_screen_tick(int64_t now_us);
void usage_screen_set_stale(bool stale);
void usage_screen_show_view(int index);
int usage_screen_current_view(void);

#endif
