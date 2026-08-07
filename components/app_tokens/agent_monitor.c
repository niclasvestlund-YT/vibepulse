#include "agent_monitor.h"

#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_monitor_policy.h"
#include "torget.h"

extern const lv_font_t plex_text_16;
extern const lv_font_t plex_ui_12;

#define COL_CLAUDE  lv_color_hex(0xFF8065)
#define COL_CODEX   lv_color_hex(0x6F78FF)
#define COL_WAIT    lv_color_hex(0xFFAE52)
#define COL_DONE    lv_color_hex(0xFF6C5F)
#define COL_ERROR   lv_color_hex(0xE0635B)
#define COL_MUTED   lv_color_hex(0x777E88)
#define COL_IDLE    lv_color_hex(0x3D424A)
#define COL_LINE    lv_color_hex(0x202328)

typedef struct {
  lv_obj_t *root;
  lv_obj_t *claude_pet;
  lv_obj_t *codex_pet;
  lv_obj_t *codex_cloud;
  lv_obj_t *codex_chevron;
  lv_obj_t *codex_underscore;
  lv_obj_t *activity;
  lv_obj_t *project;
  lv_obj_t *bars[3];
  bool claude;
} footer_view;

static struct {
  footer_view footers[2];
  tk_agent_status agents[2];
  int64_t applied_at_us;
  bool has_snapshot;
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

static lv_color_t state_color(int provider, tk_agent_state state) {
  if (state == TK_AGENT_WAITING) return COL_WAIT;
  if (state == TK_AGENT_DONE) return COL_DONE;
  if (state == TK_AGENT_ERROR) return COL_ERROR;
  if (state == TK_AGENT_IDLE || state == TK_AGENT_UNKNOWN) return COL_IDLE;
  return provider == 0 ? COL_CLAUDE : COL_CODEX;
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

static void render_footer(int provider, uint64_t packet_age_ms) {
  footer_view *footer = &mon.footers[provider];
  if (!footer->root) return;
  tk_agent_status status = mon.agents[provider];
  status.state = tk_agent_monitor_effective_state(&status, packet_age_ms);
  lv_color_t color = state_color(provider, status.state);
  const char *activity = tk_agent_monitor_activity_text(&status);

  lv_label_set_text(footer->activity, activity);
  lv_obj_set_style_text_color(footer->activity, color, 0);
  if (activity[0]) lv_obj_remove_flag(footer->activity, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(footer->activity, LV_OBJ_FLAG_HIDDEN);

  char project[TK_AGENT_PROJECT_CAP];
  uppercase_project(status.state == TK_AGENT_IDLE ||
                            status.state == TK_AGENT_UNKNOWN
                        ? "" : status.project,
                    project, sizeof project);
  char project_line[40];
  if (project[0]) snprintf(project_line, sizeof project_line,
                           "PROJEKT · %s", project);
  else project_line[0] = '\0';
  lv_label_set_text(footer->project, project_line);
  if (project_line[0]) lv_obj_remove_flag(footer->project,
                                          LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(footer->project, LV_OBJ_FLAG_HIDDEN);

  bool active = status.state == TK_AGENT_WORKING;
  if (provider == 0) {
    lv_obj_set_style_image_recolor(footer->claude_pet,
                                   active ? lv_color_white() : color, 0);
    lv_obj_set_style_image_recolor_opa(footer->claude_pet,
                                       LV_OPA_COVER, 0);
  } else {
    lv_obj_set_style_image_recolor(footer->codex_cloud, color, 0);
    lv_obj_set_style_image_recolor_opa(footer->codex_cloud,
                                       active ? LV_OPA_TRANSP : LV_OPA_COVER,
                                       0);
  }

  static const int heights[3] = {8, 18, 12};
  for (int i = 0; i < 3; i++) {
    lv_obj_set_height(footer->bars[i], heights[i]);
    lv_obj_set_style_bg_color(footer->bars[i], color, 0);
    lv_obj_set_style_opa(footer->bars[i],
                         active ? (i == 0 ? 140 : i == 2 ? 190 : 255) : 80,
                         0);
  }
}

void tk_agent_monitor_create_footer(lv_obj_t *parent, bool claude) {
  int provider = claude ? 0 : 1;
  footer_view *footer = &mon.footers[provider];
  memset(footer, 0, sizeof *footer);
  footer->root = parent;
  footer->claude = claude;

  lv_obj_t *line = bare(parent);
  lv_obj_set_pos(line, 0, 0);
  lv_obj_set_size(line, 444, 1);
  lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(line, COL_LINE, 0);

  lv_obj_t *group = bare(parent);
  lv_obj_set_size(group, 340, 54);
  lv_obj_align(group, LV_ALIGN_CENTER, 0, 4);

  footer->claude_pet = lv_image_create(group);
  lv_image_set_src(footer->claude_pet, &tk_img_claude_32);
  lv_obj_set_pos(footer->claude_pet, 8, 11);
  lv_obj_remove_flag(footer->claude_pet, LV_OBJ_FLAG_CLICKABLE);

  footer->codex_pet = bare(group);
  lv_obj_set_pos(footer->codex_pet, 8, 11);
  lv_obj_set_size(footer->codex_pet, 32, 32);
  footer->codex_cloud = lv_image_create(footer->codex_pet);
  lv_image_set_src(footer->codex_cloud, &tk_img_codex_cloud_32);
  footer->codex_chevron = lv_image_create(footer->codex_pet);
  lv_image_set_src(footer->codex_chevron, &tk_img_codex_chevron_32);
  footer->codex_underscore = lv_image_create(footer->codex_pet);
  lv_image_set_src(footer->codex_underscore, &tk_img_codex_underscore_32);
  if (claude) lv_obj_add_flag(footer->codex_pet, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(footer->claude_pet, LV_OBJ_FLAG_HIDDEN);

  lv_obj_t *copy = bare(group);
  lv_obj_set_pos(copy, 51, 7);
  lv_obj_set_size(copy, 235, 43);
  footer->activity = label(copy, &plex_text_16,
                           claude ? COL_CLAUDE : COL_CODEX);
  lv_obj_set_pos(footer->activity, 0, 0);
  lv_obj_set_size(footer->activity, 235, 22);
  lv_obj_set_style_text_letter_space(footer->activity, 1, 0);
  footer->project = lv_label_create(copy);
  lv_obj_set_style_text_font(footer->project, &plex_ui_12, 0);
  lv_obj_set_style_text_color(footer->project, COL_MUTED, 0);
  lv_obj_set_style_text_letter_space(footer->project, 1, 0);
  lv_obj_set_pos(footer->project, 0, 25);
  lv_obj_set_size(footer->project, 235, 18);

  lv_obj_t *meter = bare(group);
  lv_obj_set_pos(meter, 302, 15);
  lv_obj_set_size(meter, 28, 24);
  lv_obj_set_flex_flow(meter, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(meter, LV_FLEX_ALIGN_SPACE_BETWEEN,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  for (int i = 0; i < 3; i++) {
    footer->bars[i] = bare(meter);
    lv_obj_set_width(footer->bars[i], 4);
    lv_obj_set_style_radius(footer->bars[i], LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(footer->bars[i], LV_OPA_COVER, 0);
  }
  render_footer(provider, 0);
}

void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot,
                            int64_t now_us) {
  if (!snapshot) return;
  mon.agents[0] = snapshot->claude;
  mon.agents[1] = snapshot->codex;
  mon.applied_at_us = now_us;
  mon.has_snapshot = true;
  for (int provider = 0; provider < 2; provider++) {
    render_footer(provider, 0);
    if (tk_agent_monitor_should_keep_awake(&mon.agents[provider], "", 0))
      torget_keep_awake();
  }
}

void tk_agent_monitor_tick(int64_t now_us) {
  if (!mon.has_snapshot) return;
  uint64_t age_ms = now_us > mon.applied_at_us
                        ? (uint64_t)(now_us - mon.applied_at_us) / 1000ULL
                        : 0;
  for (int provider = 0; provider < 2; provider++)
    render_footer(provider, age_ms);
}
