#include "agent_monitor.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_monitor_policy.h"
#include "fmt_sv.h"
#include "torget.h"

extern const lv_font_t plex_num_38;
extern const lv_font_t plex_text_16;
extern const lv_font_t plex_text_17;
extern const lv_font_t plex_status_64;

#define COL_CLAUDE lv_color_hex(0xD97757)
#define COL_CODEX  lv_color_hex(0x625BFF)
#define COL_WAIT   lv_color_hex(0xFF9F2F)
#define COL_ERROR  lv_color_hex(0xE0635B)
#define COL_WHITE  lv_color_hex(0xFFFFFF)
#define COL_MUTED  lv_color_hex(0x8994A5)
#define COL_TRACK  lv_color_hex(0x1D2634)
#define COL_DOT_OFF lv_color_hex(0x2B3442)

typedef struct {
  lv_obj_t *overlay;
  lv_obj_t *provider;
  lv_obj_t *project;
  lv_obj_t *provider_dots[2];
  lv_obj_t *claude_pet;
  lv_obj_t *codex_pet;
  lv_obj_t *codex_cloud;
  lv_obj_t *codex_chevron;
  lv_obj_t *codex_underscore;
  lv_obj_t *status;
  lv_obj_t *work_dots[3];
  lv_obj_t *activity;
  lv_obj_t *usage_pct;
  lv_obj_t *usage_suffix;
  lv_obj_t *usage_fill;
  tk_agent_status agents[2];
  tk_agent_usage usage[2];
  char dismissed_event_id[2][TK_AGENT_ID_CAP];
  int selected;
  int64_t applied_at_us;
  uint8_t rendered_presence_mask;
  bool has_snapshot;
  bool long_pressed;
  tk_agent_manual_choice manual_choice;
} monitor_state;

static monitor_state mon;

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

static uint64_t packet_age_ms(int64_t now_us) {
  if (now_us > mon.applied_at_us) {
    return (uint64_t)(now_us - mon.applied_at_us) / 1000ULL;
  }
  return 0;
}

static bool is_present(int provider, int64_t now_us) {
  return tk_agent_monitor_status_present(
      &mon.agents[provider], mon.dismissed_event_id[provider],
      packet_age_ms(now_us));
}

static uint8_t current_presence_mask(int64_t now_us) {
  return tk_agent_monitor_presence_mask(
      mon.agents, mon.dismissed_event_id, packet_age_ms(now_us));
}

static lv_color_t provider_color(int provider) {
  return provider == 0 ? COL_CLAUDE : COL_CODEX;
}

static lv_color_t state_color(int provider, tk_agent_state state) {
  if (state == TK_AGENT_WAITING) return COL_WAIT;
  if (state == TK_AGENT_ERROR) return COL_ERROR;
  return provider_color(provider);
}

static const char *status_text(tk_agent_state state) {
  switch (state) {
    case TK_AGENT_WORKING: return "JOBBAR";
    case TK_AGENT_WAITING: return "VÄNTAR";
    case TK_AGENT_DONE: return "KLAR";
    case TK_AGENT_ERROR: return "FEL";
    default: return "";
  }
}

static const char *activity_text(const tk_agent_status *status) {
  if (status->state == TK_AGENT_DONE) return "VÄNTAR PÅ DIG";
  if (status->state == TK_AGENT_ERROR) return "KUNDE INTE FORTSÄTTA";
  switch (status->activity) {
    case TK_ACTIVITY_THINKING: return "TÄNKER";
    case TK_ACTIVITY_READING: return "LÄSER KOD";
    case TK_ACTIVITY_EDITING: return "ÄNDRAR FILER";
    case TK_ACTIVITY_SEARCHING: return "SÖKER I PROJEKTET";
    case TK_ACTIVITY_RUNNING: return "KÖR KOMMANDO";
    case TK_ACTIVITY_TESTING: return "KÖR TESTER";
    case TK_ACTIVITY_BUILDING: return "BYGGER PROJEKTET";
    case TK_ACTIVITY_WAITING_INPUT: return "BEHÖVER ETT SVAR";
    case TK_ACTIVITY_WAITING_APPROVAL:
      return "BEHÖVER DITT GODKÄNNANDE";
    case TK_ACTIVITY_NONE:
    case TK_ACTIVITY_UNKNOWN:
      return "";
  }
  return "";
}

static const char *usage_window_text(tk_usage_window window) {
  switch (window) {
    case TK_USAGE_SESSION: return "SESSIONEN";
    case TK_USAGE_WEEK: return "VECKAN";
    case TK_USAGE_FABLE: return "FABLE";
    case TK_USAGE_NONE: return "";
  }
  return "";
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

static void render_usage(int provider, lv_color_t color) {
  tk_agent_usage usage = mon.usage[provider];
  char value[24];
  if (!usage.has_pct || !isfinite(usage.pct)) {
    lv_label_set_text(mon.usage_pct, "–");
    lv_label_set_text(mon.usage_suffix, "");
    lv_obj_set_width(mon.usage_fill, 0);
    return;
  }

  sv_pct_1(usage.pct, value, sizeof value);
  lv_label_set_text(mon.usage_pct, value);
  char suffix[24];
  snprintf(suffix, sizeof suffix, "%% %s", usage_window_text(usage.window));
  lv_label_set_text(mon.usage_suffix, suffix);
  lv_obj_update_layout(mon.overlay);
  lv_obj_align_to(mon.usage_suffix, mon.usage_pct, LV_ALIGN_OUT_RIGHT_BOTTOM,
                  8, -5);

  double clamped = usage.pct;
  if (clamped < 0) clamped = 0;
  if (clamped > 100) clamped = 100;
  lv_obj_set_width(mon.usage_fill, (int32_t)llround(432.0 * clamped / 100.0));
  lv_obj_set_style_bg_color(mon.usage_fill, color, 0);
}

static void render_provider(int provider, int64_t now_us) {
  if (provider < 0 || !is_present(provider, now_us)) {
    mon.selected = -1;
    lv_obj_add_flag(mon.overlay, LV_OBJ_FLAG_HIDDEN);
    return;
  }

  mon.selected = provider;
  const tk_agent_status *agent = &mon.agents[provider];
  lv_color_t color = state_color(provider, agent->state);
  lv_color_t pet_color = agent->state == TK_AGENT_WORKING ? COL_WHITE : color;

  lv_label_set_text(mon.provider, provider == 0 ? "CLAUDE CODE" : "CODEX");
  char project[TK_AGENT_PROJECT_CAP];
  uppercase_project(agent->project, project, sizeof project);
  lv_label_set_text(mon.project, project);

  for (int i = 0; i < 2; i++) {
    bool active = is_present(i, now_us);
    lv_obj_set_style_bg_color(mon.provider_dots[i],
                              i == provider ? provider_color(i) :
                              (active ? COL_MUTED : COL_DOT_OFF), 0);
    lv_obj_set_style_opa(mon.provider_dots[i], active ? LV_OPA_COVER : 110, 0);
  }

  if (provider == 0) {
    lv_obj_remove_flag(mon.claude_pet, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(mon.codex_pet, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_image_recolor(mon.claude_pet, pet_color, 0);
    lv_obj_set_style_image_recolor_opa(mon.claude_pet, LV_OPA_COVER, 0);
  } else {
    lv_obj_add_flag(mon.claude_pet, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(mon.codex_pet, LV_OBJ_FLAG_HIDDEN);
    lv_opa_t recolor = agent->state == TK_AGENT_WORKING ? LV_OPA_TRANSP
                                                        : LV_OPA_COVER;
    lv_obj_set_style_image_recolor(mon.codex_cloud, color, 0);
    lv_obj_set_style_image_recolor_opa(mon.codex_cloud, recolor, 0);
    lv_obj_set_style_image_recolor(mon.codex_chevron, COL_WHITE, 0);
    lv_obj_set_style_image_recolor(mon.codex_underscore, COL_WHITE, 0);
  }

  lv_label_set_text(mon.status, status_text(agent->state));
  lv_obj_set_style_text_color(mon.status, color, 0);
  const char *activity = activity_text(agent);
  lv_label_set_text(mon.activity, activity);
  if (activity[0]) lv_obj_remove_flag(mon.activity, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(mon.activity, LV_OBJ_FLAG_HIDDEN);

  for (int i = 0; i < 3; i++) {
    lv_obj_set_style_bg_color(mon.work_dots[i], color, 0);
    lv_obj_set_style_opa(mon.work_dots[i],
                         i == 1 ? LV_OPA_COVER : (lv_opa_t)110, 0);
  }
  render_usage(provider, color);
  lv_obj_remove_flag(mon.overlay, LV_OBJ_FLAG_HIDDEN);
  lv_obj_move_to_index(mon.overlay, -1);
}

static void render_best(int64_t now_us) {
  uint8_t mask = current_presence_mask(now_us);
  bool present[2] = {(mask & 0x01U) != 0, (mask & 0x02U) != 0};
  mon.rendered_presence_mask = mask;
  int provider = tk_agent_monitor_resolve_provider(
      mon.agents, present, &mon.manual_choice);
  if (mon.manual_choice.active &&
      provider != mon.manual_choice.provider) {
    tk_agent_monitor_manual_choice_clear(&mon.manual_choice);
  }
  render_provider(provider, now_us);
}

static void overlay_event(lv_event_t *event) {
  lv_event_code_t code = lv_event_get_code(event);
  if (code == LV_EVENT_PRESSED) {
    mon.long_pressed = false;
  } else if (code == LV_EVENT_LONG_PRESSED) {
    mon.long_pressed = true;
    torget_launcher_open();
  } else if (code == LV_EVENT_CLICKED && !mon.long_pressed &&
             mon.selected >= 0) {
    int provider = mon.selected;
    snprintf(mon.dismissed_event_id[provider],
             sizeof mon.dismissed_event_id[provider], "%s",
             mon.agents[provider].event_id);
    tk_agent_monitor_manual_choice_clear(&mon.manual_choice);
    mon.selected = -1;
    lv_obj_add_flag(mon.overlay, LV_OBJ_FLAG_HIDDEN);
  }
}

static void provider_event(lv_event_t *event) {
  lv_event_code_t code = lv_event_get_code(event);
  int provider = (int)(intptr_t)lv_event_get_user_data(event);
  if (code == LV_EVENT_CLICKED) {
    lv_event_stop_bubbling(event);
    int64_t now_us = torget_now_us();
    if (is_present(provider, now_us)) {
      tk_agent_monitor_manual_choice_set(&mon.manual_choice, provider,
                                         mon.agents);
      render_provider(provider, now_us);
    }
  }
}

static void volume_event(lv_event_t *event) {
  if (lv_event_get_code(event) == LV_EVENT_CLICKED) {
    /* Task 8 kopplar på mute. Fram till dess är ytan reserverad, men ett
     * vanligt tryck får aldrig bubbla vidare och kvittera agentnotisen. */
    lv_event_stop_bubbling(event);
  }
}

void tk_agent_monitor_create(lv_obj_t *root) {
  memset(&mon, 0, sizeof mon);
  mon.selected = -1;

  mon.overlay = bare(root);
  lv_obj_set_size(mon.overlay, 480, 480);
  lv_obj_set_style_bg_color(mon.overlay, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(mon.overlay, LV_OPA_COVER, 0);
  lv_obj_add_flag(mon.overlay, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(mon.overlay, overlay_event, LV_EVENT_PRESSED, NULL);
  lv_obj_add_event_cb(mon.overlay, overlay_event, LV_EVENT_LONG_PRESSED, NULL);
  lv_obj_add_event_cb(mon.overlay, overlay_event, LV_EVENT_CLICKED, NULL);

  for (int provider = 0; provider < 2; provider++) {
    lv_obj_t *touch = bare(mon.overlay);
    lv_obj_set_size(touch, 44, 44);
    lv_obj_set_pos(touch, 18 + provider * 44, 18);
    lv_obj_add_flag(touch, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_add_event_cb(touch, provider_event, LV_EVENT_CLICKED,
                        (void *)(intptr_t)provider);
    mon.provider_dots[provider] = bare(touch);
    lv_obj_set_size(mon.provider_dots[provider], 8, 8);
    lv_obj_set_style_radius(mon.provider_dots[provider], LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(mon.provider_dots[provider], LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(mon.provider_dots[provider], COL_DOT_OFF, 0);
    lv_obj_center(mon.provider_dots[provider]);
  }

  mon.provider = label(mon.overlay, &plex_text_16, COL_WHITE);
  lv_obj_set_pos(mon.provider, 112, 28);
  lv_obj_set_style_text_letter_space(mon.provider, 2, 0);
  mon.project = label(mon.overlay, &plex_text_16, COL_MUTED);
  lv_obj_set_pos(mon.project, 252, 28);
  lv_obj_set_width(mon.project, 152);
  lv_obj_set_style_text_align(mon.project, LV_TEXT_ALIGN_RIGHT, 0);
  lv_obj_set_style_text_letter_space(mon.project, 2, 0);

  lv_obj_t *sound_touch = bare(mon.overlay);
  lv_obj_set_size(sound_touch, 44, 44);
  lv_obj_set_pos(sound_touch, 412, 18);
  lv_obj_add_flag(sound_touch,
                  LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_EVENT_BUBBLE);
  lv_obj_add_event_cb(sound_touch, volume_event, LV_EVENT_CLICKED, NULL);
  lv_obj_t *sound = label(sound_touch, LV_FONT_DEFAULT, COL_MUTED);
  lv_obj_set_size(sound, 44, 44);
  lv_obj_set_pos(sound, 0, 0);
  lv_obj_set_style_text_align(sound, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_pad_top(sound, 13, 0);
  lv_obj_set_style_opa(sound, 90, 0);
  lv_label_set_text(sound, LV_SYMBOL_VOLUME_MAX);

  mon.claude_pet = lv_image_create(mon.overlay);
  lv_image_set_src(mon.claude_pet, &tk_img_claude);
  lv_obj_set_pos(mon.claude_pet, 150, 66);
  lv_obj_remove_flag(mon.claude_pet, LV_OBJ_FLAG_CLICKABLE);

  mon.codex_pet = bare(mon.overlay);
  lv_obj_set_size(mon.codex_pet, 180, 180);
  lv_obj_set_pos(mon.codex_pet, 150, 66);
  mon.codex_cloud = lv_image_create(mon.codex_pet);
  lv_image_set_src(mon.codex_cloud, &tk_img_codex_cloud);
  lv_obj_set_pos(mon.codex_cloud, 0, 0);
  mon.codex_chevron = lv_image_create(mon.codex_pet);
  lv_image_set_src(mon.codex_chevron, &tk_img_codex_chevron);
  lv_obj_set_pos(mon.codex_chevron, 0, 0);
  mon.codex_underscore = lv_image_create(mon.codex_pet);
  lv_image_set_src(mon.codex_underscore, &tk_img_codex_underscore);
  lv_obj_set_pos(mon.codex_underscore, 0, 0);
  lv_obj_remove_flag(mon.codex_cloud, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_remove_flag(mon.codex_chevron, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_remove_flag(mon.codex_underscore, LV_OBJ_FLAG_CLICKABLE);

  mon.status = label(mon.overlay, &plex_status_64, COL_CLAUDE);
  lv_obj_set_pos(mon.status, 24, 245);
  lv_obj_set_size(mon.status, 432, 78);
  lv_obj_set_style_text_align(mon.status, LV_TEXT_ALIGN_CENTER, 0);

  lv_obj_t *work_row = bare(mon.overlay);
  lv_obj_set_size(work_row, 52, 12);
  lv_obj_set_pos(work_row, 214, 335);
  lv_obj_set_flex_flow(work_row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(work_row, LV_FLEX_ALIGN_SPACE_BETWEEN,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  for (int i = 0; i < 3; i++) {
    mon.work_dots[i] = bare(work_row);
    lv_obj_set_size(mon.work_dots[i], 8, 8);
    lv_obj_set_style_radius(mon.work_dots[i], 2, 0);
    lv_obj_set_style_bg_opa(mon.work_dots[i], LV_OPA_COVER, 0);
  }

  mon.activity = label(mon.overlay, &plex_text_16, COL_WHITE);
  lv_obj_set_pos(mon.activity, 24, 357);
  lv_obj_set_size(mon.activity, 432, 26);
  lv_obj_set_style_text_align(mon.activity, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_letter_space(mon.activity, 1, 0);

  mon.usage_pct = label(mon.overlay, &plex_num_38, COL_WHITE);
  lv_obj_set_pos(mon.usage_pct, 24, 400);
  mon.usage_suffix = label(mon.overlay, &plex_text_17, COL_MUTED);
  lv_label_set_text(mon.usage_suffix, "");

  lv_obj_t *track = bare(mon.overlay);
  lv_obj_set_size(track, 432, 4);
  lv_obj_set_pos(track, 24, 450);
  lv_obj_set_style_radius(track, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(track, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(track, COL_TRACK, 0);
  mon.usage_fill = bare(track);
  lv_obj_set_size(mon.usage_fill, 0, 4);
  lv_obj_set_style_radius(mon.usage_fill, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(mon.usage_fill, LV_OPA_COVER, 0);

  lv_obj_add_flag(mon.overlay, LV_OBJ_FLAG_HIDDEN);
  lv_obj_move_to_index(mon.overlay, -1);
}

void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot,
                            int64_t now_us) {
  if (!snapshot || !mon.overlay) return;
  mon.agents[0] = snapshot->claude;
  mon.agents[1] = snapshot->codex;
  mon.applied_at_us = now_us;
  mon.has_snapshot = true;
  if (tk_agent_monitor_should_keep_awake(
          &mon.agents[0], mon.dismissed_event_id[0], 0) ||
      tk_agent_monitor_should_keep_awake(
          &mon.agents[1], mon.dismissed_event_id[1], 0)) {
    torget_keep_awake();
  }
  render_best(now_us);
}

void tk_agent_monitor_tick(int64_t now_us) {
  uint8_t presence = current_presence_mask(now_us);
  if (tk_agent_monitor_tick_should_render(
          mon.has_snapshot, mon.selected, mon.rendered_presence_mask,
          presence)) {
    render_best(now_us);
  }
}

void tk_agent_monitor_set_usage(bool claude, tk_agent_usage usage) {
  int provider = claude ? 0 : 1;
  mon.usage[provider] = usage;
  if (mon.selected == provider) render_usage(provider,
                                               state_color(provider,
                                                           mon.agents[provider].state));
}
