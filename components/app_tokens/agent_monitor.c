#include "agent_monitor.h"

#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_completion_policy.h"
#include "agent_monitor_policy.h"
#include "torget.h"

extern const lv_font_t plex_status_64;
extern const lv_font_t plex_text_16;
extern const lv_font_t plex_text_21;
extern const lv_font_t plex_ui_14;
extern const lv_font_t plex_ui_12;

#define COL_BLACK   lv_color_hex(0x000000)
#define COL_WHITE   lv_color_hex(0xFFFFFF)
#define COL_CLAUDE  lv_color_hex(0xD97757)
#define COL_CODEX   lv_color_hex(0x3D48FF)
#define COL_WAIT    lv_color_hex(0xFFAE52)
#define COL_ERROR   lv_color_hex(0xE0635B)
#define COL_MUTED   lv_color_hex(0x858C97)
#define COL_LINE    lv_color_hex(0x202328)

typedef struct {
  lv_obj_t *root;
  lv_obj_t *claude_icon;
  lv_obj_t *codex_icon;
  lv_obj_t *title;
  lv_obj_t *activity;
  lv_obj_t *project;
} provider_lane;

typedef struct {
  lv_obj_t *root;
  lv_obj_t *provider;
  lv_obj_t *claude_icon;
  lv_obj_t *codex_icon;
  lv_obj_t *done;
  lv_obj_t *project;
  lv_obj_t *other_jobs;
} completion_view;

static struct {
  lv_obj_t *rail;
  provider_lane lanes[TK_AGENT_PROVIDER_COUNT];
  completion_view completion;
  tk_agent_snapshot snapshot;
  tk_completion_queue queue;
  int64_t applied_at_us;
  int64_t rendered_at_us;
  bool has_snapshot;
  bool suppress_click;
} mon;

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *object = lv_obj_create(parent);
  lv_obj_remove_style_all(object);
  lv_obj_set_size(object, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font,
                       lv_color_t color) {
  lv_obj_t *object = lv_label_create(parent);
  lv_obj_set_style_text_font(object, font, 0);
  lv_obj_set_style_text_color(object, color, 0);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static lv_obj_t *create_codex_icon(lv_obj_t *parent,
                                   const lv_image_dsc_t *cloud,
                                   const lv_image_dsc_t *chevron,
                                   const lv_image_dsc_t *underscore,
                                   int x, int y, int size, bool scale) {
  lv_obj_t *group = bare(parent);
  lv_obj_set_pos(group, x, y);
  lv_obj_set_size(group, size, size);

  const lv_image_dsc_t *layers[3] = { cloud, chevron, underscore };
  for (int i = 0; i < 3; i++) {
    lv_obj_t *image = lv_image_create(group);
    lv_image_set_src(image, layers[i]);
    lv_obj_remove_flag(image, LV_OBJ_FLAG_CLICKABLE);
    if (scale) lv_image_set_scale(image, 213); /* 180 px -> 150 px. */
    lv_obj_align(image, LV_ALIGN_CENTER, 0, 0);
    if (i > 0) {
      lv_obj_set_style_image_recolor(image, COL_WHITE, 0);
      lv_obj_set_style_image_recolor_opa(image, LV_OPA_COVER, 0);
    }
  }
  return group;
}

static void uppercase_project(const char *source, char *destination,
                              size_t capacity) {
  size_t index = 0;
  if (!source) source = "";
  while (source[index] && index + 1 < capacity) {
    char byte = source[index];
    destination[index] = (byte >= 'a' && byte <= 'z')
                             ? (char)(byte - 'a' + 'A') : byte;
    index++;
  }
  destination[index] = '\0';
}

static lv_color_t state_color(int provider, tk_agent_state state) {
  if (state == TK_AGENT_WAITING) return COL_WAIT;
  if (state == TK_AGENT_ERROR) return COL_ERROR;
  return provider == TK_AGENT_PROVIDER_CLAUDE ? COL_CLAUDE : COL_CODEX;
}

static const tk_agent_provider_status *provider_at(int provider) {
  return provider == TK_AGENT_PROVIDER_CLAUDE ? &mon.snapshot.claude
                                               : &mon.snapshot.codex;
}

static bool provider_present(int provider, uint64_t packet_age_ms) {
  const tk_agent_provider_status *source = provider_at(provider);
  for (uint8_t i = 0; i < source->job_count; i++) {
    if (tk_agent_monitor_status_present(&source->jobs[i], "",
                                        packet_age_ms)) return true;
  }
  return false;
}

static void set_lane_layout(provider_lane *lane, int x, int width) {
  lv_obj_set_pos(lane->root, x, 1);
  lv_obj_set_size(lane->root, width, 77);
  int text_width = width - 54;
  lv_obj_set_width(lane->title, text_width);
  lv_obj_set_width(lane->activity, text_width);
  lv_obj_set_width(lane->project, text_width);
}

static void render_lane(int provider, uint64_t packet_age_ms) {
  provider_lane *lane = &mon.lanes[provider];
  const tk_agent_provider_status *source = provider_at(provider);
  const tk_agent_status *primary = tk_agent_provider_primary(source);
  if (!primary) return;

  tk_agent_status status = *primary;
  status.state = tk_agent_monitor_effective_state(primary, packet_age_ms);
  lv_color_t color = state_color(provider, status.state);

  char title[32];
  const char *provider_name = provider == TK_AGENT_PROVIDER_CLAUDE
                                  ? "CLAUDE" : "CODEX";
  if (source->active_count > 0) {
    snprintf(title, sizeof title, "%s · %u JOBBAR", provider_name,
             (unsigned)source->active_count);
  } else {
    snprintf(title, sizeof title, "%s · KLAR", provider_name);
  }
  lv_label_set_text(lane->title, title);
  lv_obj_set_style_text_color(lane->title, color, 0);

  const char *activity = tk_agent_monitor_activity_text(&status);
  lv_label_set_text(lane->activity, activity);

  char project[TK_AGENT_PROJECT_CAP];
  uppercase_project(status.project, project, sizeof project);
  lv_label_set_text(lane->project, project);

  if (provider == TK_AGENT_PROVIDER_CLAUDE) {
    lv_obj_remove_flag(lane->claude_icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(lane->codex_icon, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(lane->claude_icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(lane->codex_icon, LV_OBJ_FLAG_HIDDEN);
  }
}

static void render_rail(uint64_t packet_age_ms) {
  if (!mon.rail) return;
  bool present[2] = {
      provider_present(TK_AGENT_PROVIDER_CLAUDE, packet_age_ms),
      provider_present(TK_AGENT_PROVIDER_CODEX, packet_age_ms),
  };
  if (!present[0] && !present[1]) {
    lv_obj_add_flag(mon.rail, LV_OBJ_FLAG_HIDDEN);
    return;
  }

  lv_obj_remove_flag(mon.rail, LV_OBJ_FLAG_HIDDEN);
  for (int provider = 0; provider < 2; provider++) {
    if (present[provider]) lv_obj_remove_flag(mon.lanes[provider].root,
                                              LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(mon.lanes[provider].root, LV_OBJ_FLAG_HIDDEN);
  }

  if (present[0] && present[1]) {
    set_lane_layout(&mon.lanes[0], 0, 218);
    set_lane_layout(&mon.lanes[1], 226, 218);
  } else {
    int provider = present[0] ? 0 : 1;
    set_lane_layout(&mon.lanes[provider], 0, 444);
  }
  for (int provider = 0; provider < 2; provider++) {
    if (present[provider]) render_lane(provider, packet_age_ms);
  }
}

static void render_completion(uint64_t now_ms) {
  const tk_completion_event *event =
      tk_completion_queue_current(&mon.queue);
  tk_completion_phase phase = tk_completion_phase_at(&mon.queue, now_ms);
  if (!event || phase == TK_COMPLETION_HIDDEN) {
    lv_obj_add_flag(mon.completion.root, LV_OBJ_FLAG_HIDDEN);
    return;
  }

  int provider = event->provider;
  lv_obj_remove_flag(mon.completion.root, LV_OBJ_FLAG_HIDDEN);
  lv_obj_move_foreground(mon.completion.root);
  lv_label_set_text(mon.completion.done, "KLAR");
  lv_obj_invalidate(mon.completion.done);
  lv_label_set_text(mon.completion.provider,
                    provider == TK_AGENT_PROVIDER_CLAUDE
                        ? "CLAUDE CODE" : "CODEX");
  lv_obj_set_style_text_color(mon.completion.provider,
                              provider == TK_AGENT_PROVIDER_CLAUDE
                                  ? COL_CLAUDE : COL_CODEX, 0);
  if (provider == TK_AGENT_PROVIDER_CLAUDE) {
    lv_obj_remove_flag(mon.completion.claude_icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(mon.completion.codex_icon, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(mon.completion.claude_icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(mon.completion.codex_icon, LV_OBJ_FLAG_HIDDEN);
  }

  char project[TK_AGENT_PROJECT_CAP];
  uppercase_project(event->project, project, sizeof project);
  lv_label_set_text(mon.completion.project, project);
  if (project[0]) lv_obj_remove_flag(mon.completion.project,
                                     LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(mon.completion.project, LV_OBJ_FLAG_HIDDEN);

  char jobs[24];
  if (event->other_active_count > 0) {
    snprintf(jobs, sizeof jobs, "%u JOBBAR",
             (unsigned)event->other_active_count);
    lv_label_set_text(mon.completion.other_jobs, jobs);
    lv_obj_remove_flag(mon.completion.other_jobs, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(mon.completion.other_jobs, LV_OBJ_FLAG_HIDDEN);
  }
}

static void completion_event(lv_event_t *event) {
  lv_event_code_t code = lv_event_get_code(event);
  if (code == LV_EVENT_LONG_PRESSED) {
    mon.suppress_click = true;
    torget_launcher_open();
    return;
  }
  if (code != LV_EVENT_CLICKED) return;
  if (mon.suppress_click) {
    mon.suppress_click = false;
    return;
  }
  tk_completion_queue_dismiss(&mon.queue);
  render_completion(mon.queue.last_now_ms);
}

static void create_lane(lv_obj_t *parent, int provider) {
  provider_lane *lane = &mon.lanes[provider];
  lane->root = bare(parent);

  lane->claude_icon = lv_image_create(lane->root);
  lv_image_set_src(lane->claude_icon, &tk_img_claude_32);
  lv_obj_set_pos(lane->claude_icon, 8, 23);
  lv_obj_set_style_image_recolor(lane->claude_icon, COL_CLAUDE, 0);
  lv_obj_set_style_image_recolor_opa(lane->claude_icon, LV_OPA_COVER, 0);

  lane->codex_icon = create_codex_icon(
      lane->root, &tk_img_codex_cloud_32, &tk_img_codex_chevron_32,
      &tk_img_codex_underscore_32, 8, 23, 32, false);

  lane->title = label(lane->root, &plex_ui_12, COL_WHITE);
  lv_obj_set_pos(lane->title, 52, 8);
  lv_obj_set_style_text_letter_space(lane->title, 1, 0);
  lane->activity = label(lane->root, &plex_text_16, COL_WHITE);
  lv_obj_set_pos(lane->activity, 52, 27);
  lane->project = label(lane->root, &plex_ui_12, COL_MUTED);
  lv_obj_set_pos(lane->project, 52, 52);
  lv_obj_set_style_text_letter_space(lane->project, 1, 0);
}

static void create_completion(lv_obj_t *app_root) {
  completion_view *view = &mon.completion;
  view->root = bare(app_root);
  lv_obj_set_pos(view->root, 0, 0);
  lv_obj_set_size(view->root, 480, 480);
  lv_obj_set_style_bg_opa(view->root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(view->root, COL_BLACK, 0);
  lv_obj_add_flag(view->root, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(view->root, completion_event, LV_EVENT_CLICKED, NULL);
  lv_obj_add_event_cb(view->root, completion_event,
                      LV_EVENT_LONG_PRESSED, NULL);

  view->provider = label(view->root, &plex_ui_14, COL_WHITE);
  lv_obj_set_pos(view->provider, 0, 28);
  lv_obj_set_size(view->provider, 480, 24);
  lv_obj_set_style_text_align(view->provider, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->provider, 3, 0);

  view->claude_icon = lv_image_create(view->root);
  lv_image_set_src(view->claude_icon, &tk_img_claude);
  lv_obj_align(view->claude_icon, LV_ALIGN_TOP_MID, 0, 57);
  lv_obj_set_style_image_recolor(view->claude_icon, COL_CLAUDE, 0);
  lv_obj_set_style_image_recolor_opa(view->claude_icon, LV_OPA_COVER, 0);

  view->codex_icon = create_codex_icon(
      view->root, &tk_img_codex_cloud, &tk_img_codex_chevron,
      &tk_img_codex_underscore, 150, 57, 180, false);

  view->done = label(view->root, &plex_status_64, COL_WHITE);
  lv_obj_set_pos(view->done, 0, 252);
  lv_obj_set_size(view->done, 480, 76);
  lv_obj_set_style_text_align(view->done, LV_TEXT_ALIGN_CENTER, 0);
  lv_label_set_text(view->done, "KLAR");

  view->project = label(view->root, &plex_text_21, COL_WHITE);
  lv_obj_set_pos(view->project, 20, 340);
  lv_obj_set_size(view->project, 440, 34);
  lv_obj_set_style_text_align(view->project, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->project, 2, 0);

  view->other_jobs = label(view->root, &plex_ui_14, COL_MUTED);
  lv_obj_set_pos(view->other_jobs, 20, 414);
  lv_obj_set_size(view->other_jobs, 440, 24);
  lv_obj_set_style_text_align(view->other_jobs, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->other_jobs, 2, 0);

  lv_obj_add_flag(view->root, LV_OBJ_FLAG_HIDDEN);
}

void tk_agent_monitor_create(lv_obj_t *app_root) {
  memset(&mon, 0, sizeof mon);
  mon.rail = bare(app_root);
  lv_obj_set_pos(mon.rail, 18, 366);
  lv_obj_set_size(mon.rail, 444, 78);
  lv_obj_set_style_bg_opa(mon.rail, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(mon.rail, COL_BLACK, 0);

  lv_obj_t *line = bare(mon.rail);
  lv_obj_set_size(line, 444, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_LINE, 0);
  for (int provider = 0; provider < 2; provider++)
    create_lane(mon.rail, provider);
  lv_obj_add_flag(mon.rail, LV_OBJ_FLAG_HIDDEN);

  create_completion(app_root);
}

void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot,
                            int64_t now_us) {
  if (!snapshot) return;
  mon.snapshot = *snapshot;
  mon.applied_at_us = now_us;
  mon.rendered_at_us = now_us;
  mon.has_snapshot = true;
  tk_completion_queue_apply(&mon.queue, snapshot,
                            now_us > 0 ? (uint64_t)now_us / 1000ULL : 0);
  render_rail(0);
  render_completion(now_us > 0 ? (uint64_t)now_us / 1000ULL : 0);

  const tk_agent_provider_status *providers[2] = {
      &snapshot->claude, &snapshot->codex,
  };
  for (int provider = 0; provider < 2; provider++) {
    for (uint8_t i = 0; i < providers[provider]->job_count; i++) {
      if (tk_agent_monitor_should_keep_awake(&providers[provider]->jobs[i],
                                             "", 0)) {
        torget_keep_awake();
        return;
      }
    }
  }
}

void tk_agent_monitor_tick(int64_t now_us) {
  if (!mon.has_snapshot) return;
  mon.rendered_at_us = now_us;
  uint64_t age_ms = now_us > mon.applied_at_us
                        ? (uint64_t)(now_us - mon.applied_at_us) / 1000ULL
                        : 0;
  render_rail(age_ms);
  render_completion(now_us > 0 ? (uint64_t)now_us / 1000ULL : 0);
}

void tk_agent_monitor_dismiss_current(void) {
  tk_completion_queue_dismiss(&mon.queue);
  render_completion(mon.queue.last_now_ms);
}
