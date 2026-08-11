#include "agent_monitor.h"

#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_completion_policy.h"
#include "agent_monitor_policy.h"
#include "torget.h"
#include "vibepulse_layout.generated.h"

extern const lv_font_t plex_status_64;
extern const lv_font_t plex_text_21;
extern const lv_font_t plex_ui_14;

#define COL_BLACK   lv_color_hex(VP_COLOR_BACKGROUND)
#define COL_WHITE   lv_color_hex(VP_COLOR_TEXT)
#define COL_CLAUDE  lv_color_hex(VP_COLOR_CLAUDE)
#define COL_CODEX   lv_color_hex(VP_COLOR_CODEX)
#define COL_MUTED   lv_color_hex(VP_COLOR_MUTED)

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

static void create_completion(lv_obj_t *app_root) {
  completion_view *view = &mon.completion;
  view->root = bare(app_root);
  lv_obj_set_pos(view->root, 0, 0);
  lv_obj_set_size(view->root, VP_SCREEN_W, VP_SCREEN_H);
  lv_obj_set_style_bg_opa(view->root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(view->root, COL_BLACK, 0);
  lv_obj_add_flag(view->root, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(view->root, completion_event, LV_EVENT_CLICKED, NULL);
  lv_obj_add_event_cb(view->root, completion_event,
                      LV_EVENT_LONG_PRESSED, NULL);

  view->provider = label(view->root, &plex_ui_14, COL_WHITE);
  lv_obj_set_pos(view->provider, 0, 28);
  lv_obj_set_size(view->provider, VP_SCREEN_W, 24);
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
  lv_obj_set_size(view->done, VP_SCREEN_W, 76);
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
  render_completion(now_us > 0 ? (uint64_t)now_us / 1000ULL : 0);
}

void tk_agent_monitor_dismiss_current(void) {
  tk_completion_queue_dismiss(&mon.queue);
  render_completion(mon.queue.last_now_ms);
}
