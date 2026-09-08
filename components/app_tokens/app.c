#include "app_tokens.h"

#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "needs_you_net.h"
#include "agent_status_source_policy.h"
#if defined(ESP_PLATFORM) && (CONFIG_TK_VIBEPULSE_INTERACTION_RELAY || \
                              CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY)
#include "interaction_relay_net.h"
#endif
#include "torget.h"
#include "usage_screen.h"
#include "settings_menu.h"

#ifdef ESP_PLATFORM
#include "secrets.h"
#endif

#ifdef ESP_PLATFORM
#include "torget_http.h"
#endif

extern const lv_font_t plex_icon_64;

#define STALE_AFTER_US (120LL * 1000000LL)
#define TICK_EVERY_MS 100

static struct {
  int64_t last_success_us;
  bool has_data;
  bool stale;
  tk_agent_source_policy agent_source;
} app;

void tokens_apply(const tk_tokens *tokens) {
  torget_data_alive(); /* forsta riktiga datan tar ner bootskarment */
  if (!tokens) return;
  int64_t now_us = torget_now_us();
  usage_screen_apply_tokens(tokens);

  /* A successful parse is the authority for transport freshness. Clear the
   * screen synchronously instead of depending on the next 100 ms LVGL tick:
   * a delayed/starved timer must not leave old STALE copy over fresh values.
   * Do this unconditionally so app/ui bookkeeping can self-heal if they ever
   * drift apart. Source-level stale flags remain owned by each parsed quota. */
  double rate = tokens->day_tokens_per_hour / 1e6;

  app.has_data = true;
  app.last_success_us = now_us;
  app.stale = false;
  usage_screen_set_stale(false);
  if (rate > 0.0) torget_keep_awake();
}

void tokens_apply_agent_status(const tk_agent_snapshot *snapshot) {
  if (!snapshot) return;
  int64_t now_us = torget_now_us();
  uint64_t now_ms = now_us > 0 ? (uint64_t)now_us / 1000u : 0;
  tk_agent_source_note_lan(&app.agent_source, now_ms);
  usage_screen_apply_agent(snapshot, now_us);
}

bool tokens_apply_agent_status_relay(const tk_agent_snapshot *snapshot,
                                     int64_t now_us) {
  if (!snapshot) return false;
  uint64_t now_ms = now_us > 0 ? (uint64_t)now_us / 1000u : 0;
  if (!tk_agent_source_allow_relay(&app.agent_source, now_ms)) return false;
  tk_agent_source_note_relay(&app.agent_source, now_ms);
  usage_screen_apply_agent_status_relay(snapshot, now_us);
  return true;
}

bool tokens_clear_agent_status_relay(int64_t now_us) {
  uint64_t now_ms = now_us > 0 ? (uint64_t)now_us / 1000u : 0;
  if (!tk_agent_source_should_clear_relay(&app.agent_source, now_ms)) {
    return false;
  }
  tk_agent_snapshot empty = {0};
  usage_screen_apply_agent_status_relay(&empty, now_us);
  return true;
}

void tokens_apply_max_tracker(const tk_max_tracker *t) {
  if (!t) return;
  usage_screen_apply_max_tracker(t);
}

void tokens_apply_github(const tk_github_status *status) {
  if (!status || !status->enabled) return;
  usage_screen_apply_github(status);
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
  tk_agent_source_policy_init(&app.agent_source);
  tk_labs_init();
  const tg_settings_labs labs = {
    .name = tk_labs_name, .selected = tk_labs_selected,
    .toggle = tk_labs_toggle, .pending = tk_labs_pending,
    .storage_error = tk_labs_storage_error,
  };
  torget_settings_bind_labs(&labs);
  usage_screen_create(root);
  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);

#ifdef ESP_PLATFORM
  (void)torget_cloud_io_init();
  tokens_net_start();
  tokens_agent_net_start();
  tokens_github_net_start();
  tokens_needs_you_net_start();
#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY || \
    CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
  tokens_interaction_relay_net_start();
#endif
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
