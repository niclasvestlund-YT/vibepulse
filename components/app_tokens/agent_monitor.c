#include "agent_monitor.h"

#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_completion_policy.h"
#include "agent_monitor_policy.h"
#include "torget.h"
#include "vibepulse_layout.generated.h"

extern const lv_font_t plex_attention_18;
extern const lv_font_t plex_attention_25;
extern const lv_font_t plex_attention_52;
extern const lv_font_t plex_ui_14;

#define COL_BLACK   lv_color_hex(VP_COLOR_BACKGROUND)
#define COL_WHITE   lv_color_hex(VP_COLOR_TEXT)
#define COL_CLAUDE  lv_color_hex(VP_COLOR_CLAUDE)
#define COL_CODEX   lv_color_hex(VP_COLOR_CODEX)
#define COL_MUTED   lv_color_hex(VP_COLOR_MUTED)

typedef struct {
  lv_obj_t *root;
  lv_obj_t *outline;
  lv_obj_t *provider;
  lv_obj_t *icon_ring;
  lv_obj_t *claude_icon;
  lv_obj_t *codex_icon;
  lv_obj_t *title;
  lv_obj_t *project;
  lv_obj_t *detail;
  lv_obj_t *dismiss;
} completion_view;

static struct {
  completion_view completion;
  tk_agent_snapshot snapshot;
  tk_completion_queue queue;
  tk_completion_render_key rendered_completion;
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

static lv_obj_t *create_codex_icon(lv_obj_t *parent, int x, int y) {
  lv_obj_t *image = lv_image_create(parent);
  lv_image_set_src(image, &tk_img_codex);
  lv_obj_remove_flag(image, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_pos(image, x, y);
  return image;
}

/* Pulsen: accentkonturen och ikonringen andas i opacitet under larmets
 * PULSE-fas. Rörelse i befintliga element, aldrig nya ytor; texten står
 * still för läsbarhet. Cyklerna à 1200 ms fyller TK_COMPLETION_PULSE_MS
 * (45 s sedan 2026-08-14 — 4,8 s missades i praktiken), sedan vilar allt
 * på full opacitet i STATIC. */
#define COMPLETION_PULSE_CYCLE_MS 1200U
#define COMPLETION_PULSE_MIN_OPA 100

static void completion_pulse_exec(void *var, int32_t value) {
  (void)var;
  lv_obj_set_style_border_opa(mon.completion.outline, (lv_opa_t)value, 0);
  lv_obj_set_style_border_opa(mon.completion.icon_ring, (lv_opa_t)value, 0);
}

static void completion_pulse_stop(void) {
  lv_anim_delete(&mon.completion, completion_pulse_exec);
  lv_obj_set_style_border_opa(mon.completion.outline, LV_OPA_COVER, 0);
  lv_obj_set_style_border_opa(mon.completion.icon_ring, LV_OPA_COVER, 0);
}

static void completion_pulse_start(void) {
  completion_pulse_stop();
  lv_anim_t anim;
  lv_anim_init(&anim);
  lv_anim_set_var(&anim, &mon.completion);
  lv_anim_set_exec_cb(&anim, completion_pulse_exec);
  lv_anim_set_values(&anim, LV_OPA_COVER, COMPLETION_PULSE_MIN_OPA);
  lv_anim_set_duration(&anim, COMPLETION_PULSE_CYCLE_MS / 2);
  lv_anim_set_playback_duration(&anim, COMPLETION_PULSE_CYCLE_MS / 2);
  lv_anim_set_repeat_count(&anim,
                           TK_COMPLETION_PULSE_MS / COMPLETION_PULSE_CYCLE_MS);
  lv_anim_set_path_cb(&anim, lv_anim_path_ease_in_out);
  lv_anim_start(&anim);
}

static void render_completion(uint64_t now_ms) {
  tk_completion_phase phase = tk_completion_phase_at(&mon.queue, now_ms);
  const tk_completion_event *event =
      tk_completion_queue_current(&mon.queue);
  bool visible = event && phase != TK_COMPLETION_HIDDEN;
  if (!tk_completion_render_key_update(&mon.rendered_completion, event,
                                       visible)) return;
  if (!visible) {
    completion_pulse_stop();
    lv_obj_add_flag(mon.completion.root, LV_OBJ_FLAG_HIDDEN);
    return;
  }

  int provider = event->provider;
  lv_color_t accent = provider == TK_AGENT_PROVIDER_CLAUDE
                          ? COL_CLAUDE : COL_CODEX;
  const char *provider_name = provider == TK_AGENT_PROVIDER_CLAUDE
                                  ? "CLAUDE" : "CODEX";
  lv_obj_remove_flag(mon.completion.root, LV_OBJ_FLAG_HIDDEN);
  lv_obj_move_foreground(mon.completion.root);
  lv_label_set_text(mon.completion.provider, provider_name);
  lv_obj_set_style_text_color(mon.completion.provider, accent, 0);
  lv_obj_set_style_border_color(mon.completion.outline, accent, 0);
  lv_obj_set_style_border_color(mon.completion.icon_ring, accent, 0);
  if (provider == TK_AGENT_PROVIDER_CLAUDE) {
    lv_obj_remove_flag(mon.completion.claude_icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(mon.completion.codex_icon, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(mon.completion.claude_icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(mon.completion.codex_icon, LV_OBJ_FLAG_HIDDEN);
  }

  char project[TK_AGENT_PROJECT_CAP];
  tk_agent_monitor_project_label(event->project, project, sizeof project);
  lv_label_set_text(mon.completion.project, project);
  lv_obj_set_style_text_color(mon.completion.project, accent, 0);
  if (project[0]) lv_obj_remove_flag(mon.completion.project,
                                     LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(mon.completion.project, LV_OBJ_FLAG_HIDDEN);

  char detail[32];
  const char *title = "DONE";
  if (event->state == TK_AGENT_WAITING) {
    title = "NEEDS YOU";
    if (event->same_state_count > 1) {
      snprintf(detail, sizeof detail, "%u AGENTS WAITING",
               (unsigned)event->same_state_count);
    } else {
      snprintf(detail, sizeof detail, "%s",
               provider == TK_AGENT_PROVIDER_CLAUDE
                   ? "CLAUDE IS WAITING" : "CODEX IS WAITING");
    }
  } else if (event->state == TK_AGENT_ERROR) {
    title = "ERROR";
    if (event->same_state_count > 1) {
      snprintf(detail, sizeof detail, "%u AGENTS NEED ATTENTION",
               (unsigned)event->same_state_count);
    } else {
      snprintf(detail, sizeof detail, "%s",
               provider == TK_AGENT_PROVIDER_CLAUDE
                   ? "CLAUDE NEEDS ATTENTION" : "CODEX NEEDS ATTENTION");
    }
  } else if (event->same_state_count > 1) {
    snprintf(detail, sizeof detail, "%u AGENTS FINISHED",
             (unsigned)event->same_state_count);
  } else {
    snprintf(detail, sizeof detail, "%s",
             provider == TK_AGENT_PROVIDER_CLAUDE
                 ? "CLAUDE FINISHED" : "CODEX FINISHED");
  }
  lv_label_set_text(mon.completion.title, title);
  lv_label_set_text(mon.completion.detail, detail);
  /* Ny alert eller nytt tillstånd på skärmen = ny puls. Startar även när
   * antalet i samma tillstånd växer — ny information förtjänar en andning. */
  completion_pulse_start();
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

  view->outline = bare(view->root);
  lv_obj_set_pos(view->outline, 8, 8);
  lv_obj_set_size(view->outline, 464, 464);
  lv_obj_set_style_bg_opa(view->outline, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_opa(view->outline, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(view->outline, 6, 0);
  lv_obj_set_style_radius(view->outline, 36, 0);

  view->provider = label(view->root, &plex_attention_18, COL_WHITE);
  lv_obj_set_pos(view->provider, 20, 31);
  lv_obj_set_size(view->provider, 440, 25);
  lv_obj_set_style_text_align(view->provider, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->provider, 3, 0);

  view->icon_ring = bare(view->root);
  lv_obj_set_pos(view->icon_ring, 172, 77);
  lv_obj_set_size(view->icon_ring, 136, 136);
  lv_obj_set_style_bg_opa(view->icon_ring, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_opa(view->icon_ring, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(view->icon_ring, 3, 0);
  lv_obj_set_style_radius(view->icon_ring, LV_RADIUS_CIRCLE, 0);

  lv_obj_t *claude_group = bare(view->root);
  lv_obj_set_pos(claude_group, 184, 89);
  lv_obj_set_size(claude_group, 112, 112);
  view->claude_icon = lv_image_create(claude_group);
  lv_image_set_src(view->claude_icon, &tk_img_claude);
  lv_obj_set_size(view->claude_icon, 112, 112);
  lv_image_set_inner_align(view->claude_icon, LV_IMAGE_ALIGN_STRETCH);
  lv_obj_set_pos(view->claude_icon, 0, 0);
  lv_obj_set_style_image_recolor(view->claude_icon, COL_CLAUDE, 0);
  lv_obj_set_style_image_recolor_opa(view->claude_icon, LV_OPA_COVER, 0);

  view->codex_icon = create_codex_icon(view->root, 184, 89);

  view->title = label(view->root, &plex_attention_52, COL_WHITE);
  lv_obj_set_pos(view->title, 14, 246);
  lv_obj_set_size(view->title, 452, 68);
  lv_obj_set_style_text_align(view->title, LV_TEXT_ALIGN_CENTER, 0);

  view->project = label(view->root, &plex_attention_25, COL_WHITE);
  lv_obj_set_pos(view->project, 20, 321);
  lv_obj_set_size(view->project, 440, 34);
  lv_obj_set_style_text_align(view->project, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->project, 2, 0);

  view->detail = label(view->root, &plex_ui_14, COL_MUTED);
  lv_obj_set_pos(view->detail, 20, 365);
  lv_obj_set_size(view->detail, 440, 25);
  lv_obj_set_style_text_align(view->detail, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->detail, 2, 0);

  view->dismiss = label(view->root, &plex_ui_14, COL_MUTED);
  lv_obj_set_pos(view->dismiss, 20, 430);
  lv_obj_set_size(view->dismiss, 440, 26);
  lv_obj_set_style_text_align(view->dismiss, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(view->dismiss, 2, 0);
  lv_label_set_text(view->dismiss, "TAP TO DISMISS");

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
