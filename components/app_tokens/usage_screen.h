#ifndef USAGE_SCREEN_H
#define USAGE_SCREEN_H

#include <stdbool.h>
#include <stdint.h>

#include "lvgl.h"

#include "app_tokens_config.h"
#include "agent_status.h"
#include "github_status.h"
#include "max_tracker.h"
#include "tokens.h"

/* Six base tiles + the optional GitHub tile + the always-present Value tile. */
#define TK_USAGE_SCREEN_VIEWS (6 + TK_GITHUB_SCREEN_ENABLED + 1)

void usage_screen_create(lv_obj_t *root);
void usage_screen_apply_tokens(const tk_tokens *tokens);
void usage_screen_apply_agent(const tk_agent_snapshot *snapshot,
                              int64_t now_us);
void usage_screen_apply_agent_status_relay(
    const tk_agent_snapshot *snapshot, int64_t now_us);
void usage_screen_apply_max_tracker(const tk_max_tracker *t);
void usage_screen_apply_github(const tk_github_status *status);
void usage_screen_tick(int64_t now_us);
void usage_screen_set_stale(bool stale);
void usage_screen_show_view(int index);
int usage_screen_current_view(void);
/* True when a tile was created at `index`. The tileview only knows the tiles
 * it was given, so every index below TK_USAGE_SCREEN_VIEWS must answer yes or
 * a swipe has a hole to fall into — the bug #92 fixed, and what the
 * simulator's --vibepulse-view-qa proves for both GitHub settings. */
bool usage_screen_has_view(int index);

#endif
