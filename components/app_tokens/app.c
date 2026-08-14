#include "app_tokens.h"

#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "torget.h"
#include "usage_screen.h"

#ifdef ESP_PLATFORM
#include "secrets.h"
#endif

extern const lv_font_t plex_icon_64;

#define STALE_AFTER_US (120LL * 1000000LL)
#define TICK_EVERY_MS 100

static struct {
  int64_t last_success_us;
  bool has_data;
  bool stale;
} app;

void tokens_apply(const tk_tokens *tokens) {
  torget_data_alive(); /* forsta riktiga datan tar ner bootskarment */
  if (!tokens) return;
  int64_t now_us = torget_now_us();
  usage_screen_apply_tokens(tokens);

  double rate = tokens->day_tokens_per_hour / 1e6;

  app.has_data = true;
  app.last_success_us = now_us;
  if (rate > 0.0) torget_keep_awake();
}

void tokens_apply_agent_status(const tk_agent_snapshot *snapshot) {
  usage_screen_apply_agent(snapshot, torget_now_us());
}

void tokens_apply_max_tracker(const tk_max_tracker *t) {
  if (!t) return;
  usage_screen_apply_max_tracker(t);
}

void tokens_show_view(int index) {
  usage_screen_show_view(index);
}

static void tick_cb(lv_timer_t *timer) {
  (void)timer;
  int64_t now_us = torget_now_us();
  usage_screen_tick(now_us);

#if defined(ESP_PLATFORM) && defined(TK_AGENT_DEMO)
  static int demo_stage = -1;
  int next_stage = (int)((now_us / 5000000LL) % 4);
  if (next_stage != demo_stage) {
    demo_stage = next_stage;
    tk_agent_snapshot snapshot = {0};
    snapshot.claude.active_count = demo_stage == 2 ? 0 : 1;
    snapshot.claude.job_count = 1;
    tk_agent_status *claude = &snapshot.claude.jobs[0];
    snprintf(claude->task_id, sizeof claude->task_id,
             "demo-task");
    snprintf(claude->event_id, sizeof claude->event_id,
             "demo-%lld-%d", (long long)(now_us / 20000000LL), demo_stage);
    snprintf(claude->project, sizeof claude->project,
             "Torget");
    claude->state = (tk_agent_state[]){TK_AGENT_WORKING,
                                       TK_AGENT_WAITING,
                                       TK_AGENT_DONE,
                                       TK_AGENT_ERROR}[demo_stage];
    claude->activity = demo_stage == 0 ? TK_ACTIVITY_TESTING :
                        demo_stage == 1 ? TK_ACTIVITY_WAITING_APPROVAL :
                                          TK_ACTIVITY_NONE;
    tokens_apply_agent_status(&snapshot);
  }
#endif

  bool stale = app.has_data && now_us - app.last_success_us > STALE_AFTER_US;
  if (stale != app.stale) {
    app.stale = stale;
    usage_screen_set_stale(stale);
  }
}

void tokens_net_start(void);

static void create(lv_obj_t *root) {
  memset(&app, 0, sizeof app);
  usage_screen_create(root);
  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);

#ifdef ESP_PLATFORM
  tokens_net_start();
  tokens_agent_net_start();
#endif
}

const torget_app_t tokens_app = {
  .api_version = TORGET_APP_API_VERSION,
  .name = "VIBEPULSE",
  .icon = {
    .font = &plex_icon_64,
    .glyph = "V",
    .plate_hex = 0x181636,
    .glyph_hex = 0xFFFFFF,
    .dot_hex = 0x7770FF,
  },
  .create = create,
  .enter = NULL,
  .leave = NULL,
};
