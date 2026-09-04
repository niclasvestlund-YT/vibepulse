#include "agent_monitor.h"

#include <stdio.h>
#include <string.h>

#include "agent_assets.h"
#include "agent_completion_policy.h"
#include "agent_monitor_policy.h"
#include "needs_you_policy.h"
#include "interaction_relay_policy.h"
#include "torget.h"
#include "vibepulse_layout.generated.h"

extern const lv_font_t plex_attention_18;
extern const lv_font_t plex_attention_25;
extern const lv_font_t plex_attention_52;
extern const lv_font_t plex_ui_14;
extern const lv_font_t plex_ui_16;
extern const lv_font_t plex_ui_21; /* full ASCII incl. lowercase — long-question step-down */
/* Needs You v2 takeover fonts. The command payload is the one element the
 * design keeps in real mono; the question/title need a shelf-readable full-
 * ASCII face (plex_body_27). headline_48 carries the one-word heroes. The
 * attract project line deliberately uses ui_21: project labels may contain
 * digits and .-_?, which the uppercase-only text_21 raster cannot draw. */
extern const lv_font_t plex_body_27;
extern const lv_font_t plex_mono_40;
extern const lv_font_t plex_headline_48;

#define COL_BLACK   lv_color_hex(VP_COLOR_BACKGROUND)
#define COL_WHITE   lv_color_hex(VP_COLOR_TEXT)
#define COL_CLAUDE  lv_color_hex(VP_COLOR_CLAUDE)
#define COL_CODEX   lv_color_hex(VP_COLOR_CODEX)
#define COL_MUTED   lv_color_hex(VP_COLOR_MUTED)
#define COL_TRACK   lv_color_hex(VP_COLOR_TRACK)
#define COL_HAIR    lv_color_hex(VP_COLOR_HAIRLINE)
/* Two v2-only tones from the approved design: a dimmer label grey, and the one
 * restrained red the design law allows — for DENY, and DENY only. */
#define COL_DIM     lv_color_hex(0x5C687B)
#define COL_RED     lv_color_hex(0xE5484D)

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

/* The interactive "Needs You" takeover (approved v2 direction). A two-stage
 * summon: ATTRACT is the across-the-room alert with a depleting countdown ring;
 * a tap opens the DECISION screen (question / approval / private); an APPROVE
 * shows a static PAYOFF beat. One object tree, built hidden, groups shown per
 * stage. The policy stays authoritative — this only paints what it allows. */
typedef enum { NY_ATTRACT, NY_DECISION, NY_PAYOFF } ny_stage;

typedef struct {
  lv_obj_t *root;
  lv_obj_t *frame;        /* the accent border, shared by every stage */

  /* ATTRACT */
  lv_obj_t *a_group;
  lv_obj_t *a_ring;
  lv_obj_t *a_mascot;
  lv_obj_t *a_codex_icon;
  lv_obj_t *a_word;       /* NEEDS YOU */
  lv_obj_t *a_project;
  lv_obj_t *a_tap;        /* TAP TO ANSWER */

  /* Shared decision header: ring + mascot + eyebrow */
  lv_obj_t *h_group;
  lv_obj_t *h_ring;
  lv_obj_t *h_mascot;
  lv_obj_t *h_codex_icon;
  lv_obj_t *h_eyebrow;    /* PROVIDER NEEDS YOU · PROJECT */

  /* QUESTION body */
  lv_obj_t *q_group;
  lv_obj_t *q_prompt;
  lv_obj_t *q_card;
  lv_obj_t *q_rec;        /* CLAUDE RECOMMENDS */
  lv_obj_t *q_title;
  lv_obj_t *q_sub;
  lv_obj_t *q_footer;     /* N MORE OPTION(S) IN TERMINAL */

  /* APPROVAL body */
  lv_obj_t *p_group;
  lv_obj_t *p_desc;
  lv_obj_t *p_chip;
  lv_obj_t *p_chip_lbl;
  lv_obj_t *p_cmd;        /* the command, in mono */

  /* Shared buttons */
  lv_obj_t *approve;
  lv_obj_t *approve_label;
  lv_obj_t *deny;
  lv_obj_t *leave;

  /* PRIVATE */
  lv_obj_t *pv_group;
  lv_obj_t *pv_ring;
  lv_obj_t *pv_mascot;
  lv_obj_t *pv_codex_icon;
  lv_obj_t *pv_title;
  lv_obj_t *pv_sub;
  lv_obj_t *pv_tap;

  /* PAYOFF */
  lv_obj_t *po_group;
  lv_obj_t *po_mascot;
  lv_obj_t *po_codex_icon;
  lv_obj_t *po_word;      /* ON IT. */
  lv_obj_t *po_echo;      /* the verbatim approved item */
  lv_obj_t *po_spark[5];
} needs_you_view;

/* Enough of the decision + stage to know when a repaint is actually needed. */
typedef struct {
  bool valid;
  bool visible;
  bool offer_approve;
  bool offer_deny;
  bool marked;
  bool has_title;
  uint8_t kind;
  uint8_t stage;
  uint8_t options_total;
  uint8_t provider;
  char request_id[TK_PENDING_ID_CAP];
} needs_you_key;

static struct {
  completion_view completion;
  needs_you_view needs_you;
  tk_needs_you_state needs_you_state;
  tk_ir_policy interaction_policy;
  needs_you_key needs_you_rendered;
  bool needs_you_visible; /* read by render_completion to yield the screen */
  ny_stage stage;
  char stage_id[TK_PENDING_ID_CAP]; /* the interaction the stage belongs to */
  char echo[TK_PENDING_TITLE_CAP];  /* the approved item, for the payoff beat */
  int64_t payoff_until_us;
  tk_agent_provider payoff_provider;
  bool has_payoff_provider;
  tk_agent_monitor_needs_you_cb tk_needs_you_cb;
  tk_agent_snapshot snapshot;
  tk_completion_queue queue;
  tk_completion_render_key rendered_completion;
  int64_t applied_at_us;
  int64_t rendered_at_us;
  bool has_snapshot;
  bool suppress_click;
  tk_agent_render_stats render_stats;
  uint16_t rendered_ring_permille;
  bool rendered_ring_valid;
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
  /* The interactive takeover owns the screen while it is up: a live blocked
   * session outranks the passive DONE/NEEDS-YOU pulse, and folding it into the
   * render key means the pulse cleanly reappears the moment the takeover
   * leaves. */
  bool visible = event && phase != TK_COMPLETION_HIDDEN && !mon.needs_you_visible;
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

/* ---------------------------------------------------------------- Needs You */

extern const lv_font_t plex_mono_24;

static void render_needs_you(void);

static void ny_show(lv_obj_t *object, bool shown) {
  if (shown) lv_obj_remove_flag(object, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(object, LV_OBJ_FLAG_HIDDEN);
}

/* The one dynamic mono string is the tool chip; the mockup shows it lowercase. */
static void ny_ascii_lower(const char *source, char *destination, size_t cap) {
  size_t i = 0;
  if (!destination || cap == 0) return;
  for (; source && source[i] && i + 1 < cap; i++) {
    char c = source[i];
    destination[i] = (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
  }
  destination[i] = '\0';
}

typedef struct {
  bool private_fallback;
  bool can_approve;
  const lv_font_t *prompt_font;
  const lv_font_t *command_font;
  int tool_chip_width;
} ny_physical_fit;

/* The status parser has already validated UTF-8. Keep glyph validation
 * independent of LVGL private headers nevertheless: this bounded iterator
 * consumes one validated scalar and never reads beyond its terminating NUL. */
static uint32_t ny_utf8_next(const char *text, uint32_t *offset) {
  const unsigned char *s = (const unsigned char *)text + *offset;
  uint32_t cp;
  unsigned count;
  if (s[0] < 0x80) { cp = s[0]; count = 1; }
  else if ((s[0] & 0xe0) == 0xc0) {
    cp = s[0] & 0x1f;
    count = 2;
  } else if ((s[0] & 0xf0) == 0xe0) {
    cp = s[0] & 0x0f;
    count = 3;
  } else {
    cp = s[0] & 0x07;
    count = 4;
  }
  for (unsigned i = 1; i < count; i++) cp = (cp << 6) | (s[i] & 0x3f);
  *offset += count;
  return cp;
}

/* A verdict is only actionable when the exact bytes which give it meaning can
 * be painted. This is deliberately the same actual-font predicate used by the
 * renderer: no placeholder glyph, dot-mode clipping, or hidden field can
 * remain approvable. */
static bool ny_text_fits(const char *text, const lv_font_t *font,
                         int max_w, int max_h, bool one_line) {
  if (!text || !text[0]) return false;
  uint32_t offset = 0;
  int ink_top = 32767, ink_bottom = -32768;
  int pen_x = 0, ink_left = 32767, ink_right = -32768;
  int min_left_overhang = 0, max_right_overhang = 0;
  while (text[offset]) {
    uint32_t current = ny_utf8_next(text, &offset);
    uint32_t next_offset = offset;
    uint32_t next = text[next_offset]
                        ? ny_utf8_next(text, &next_offset) : 0;
    lv_font_glyph_dsc_t glyph;
    bool found = lv_font_get_glyph_dsc(font, &glyph, current, next);
    if (!found || glyph.is_placeholder) {
      if (found) lv_font_glyph_release_draw_data(&glyph);
      return false;
    }
    if (glyph.box_h) {
      int bottom = (int)font->line_height - (int)font->base_line - glyph.ofs_y;
      int top = bottom - glyph.box_h;
      int left = pen_x + glyph.ofs_x;
      int right = left + glyph.box_w;
      if (top < ink_top) ink_top = top;
      if (bottom > ink_bottom) ink_bottom = bottom;
      if (left < ink_left) ink_left = left;
      if (right > ink_right) ink_right = right;
      int overhang = glyph.ofs_x + glyph.box_w - glyph.adv_w;
      if (glyph.ofs_x < min_left_overhang) min_left_overhang = glyph.ofs_x;
      if (overhang > max_right_overhang) max_right_overhang = overhang;
    }
    pen_x += glyph.adv_w;
    lv_font_glyph_release_draw_data(&glyph);
  }
  lv_point_t size;
  lv_text_get_size(&size, text, font, 0, 0,
                   one_line ? LV_COORD_MAX : max_w, LV_TEXT_FLAG_NONE);
  int final_ink_bottom = size.y - (int)font->line_height + ink_bottom;
  bool vertical_ink_inside = ink_top >= 0 &&
                              ink_bottom <= (int)font->line_height &&
                              final_ink_bottom <= max_h;
  bool horizontal_ink_inside = one_line
      ? ink_left >= 0 && ink_right <= max_w
      : min_left_overhang >= 0 &&
            size.x + max_right_overhang <= max_w;
  return size.x <= max_w && size.y <= max_h && vertical_ink_inside &&
         horizontal_ink_inside;
}

static ny_physical_fit ny_physical_fit_of(const tk_pending_interaction *p,
                                           const tk_needs_you_view *decision) {
  ny_physical_fit fit = {.private_fallback = true,
                         .prompt_font = &plex_body_27,
                         .command_font = &plex_mono_40,
                         .tool_chip_width = 58};
  if (!p || !decision || !decision->visible) return fit;

  if (decision->kind == TK_PENDING_QUESTION) {
    bool prompt_ok = p->has_prompt &&
        ny_text_fits(p->prompt, &plex_body_27, 300, 68, false);
    if (!prompt_ok && p->has_prompt &&
        ny_text_fits(p->prompt, &plex_ui_21, 300, 68, false)) {
      prompt_ok = true;
      fit.prompt_font = &plex_ui_21;
    }
    if (!prompt_ok) return fit;
    /* An unmarked prompt is intentionally alert-only, but it is not private
     * when the complete prompt itself is readable. */
    if (!p->marked) {
      fit.private_fallback = false;
      return fit;
    }
    if (!p->has_title ||
        !ny_text_fits(p->title, &plex_body_27, 392, 34, true)) return fit;
    if (p->has_subtitle &&
        !ny_text_fits(p->subtitle, &plex_ui_16, 392, 20, true)) return fit;
    fit.private_fallback = false;
    fit.can_approve = decision->offer_approve;
    return fit;
  }

  if (!p->has_title) return fit;
  bool command_ok = ny_text_fits(p->title, &plex_mono_40, 432, 62, false);
  if (!command_ok && ny_text_fits(p->title, &plex_mono_24, 432, 62, false)) {
    command_ok = true;
    fit.command_font = &plex_mono_24;
  }
  if (!command_ok) return fit;
  if (p->has_subtitle &&
      !ny_text_fits(p->subtitle, &plex_body_27, 300, 68, false)) return fit;
  if (p->has_tool) {
    char tool[TK_PENDING_TOOL_CAP];
    ny_ascii_lower(p->tool, tool, sizeof tool);
    lv_point_t tool_size;
    lv_text_get_size(&tool_size, tool, &plex_ui_14, 0, 0, LV_COORD_MAX,
                     LV_TEXT_FLAG_NONE);
    if (!ny_text_fits(tool, &plex_ui_14, 192, 18, true)) return fit;
    fit.tool_chip_width = tool_size.x + 16;
    if (fit.tool_chip_width < 58) fit.tool_chip_width = 58;
    if (fit.tool_chip_width > 208) return fit;
  }
  fit.private_fallback = false;
  fit.can_approve = decision->offer_approve;
  return fit;
}

/* One signed verdict leaving the glass. Every decision is re-checked against
 * the policy here; the render layer never decides. An APPROVE opens the static
 * payoff beat, echoing the verbatim item it just committed. */
static void needs_you_resolve(tk_needs_you_verdict verdict) {
  const tk_pending_interaction *p = &mon.snapshot.pending;
  tk_needs_you_view decision = tk_needs_you_view_of(&mon.needs_you_state, p);
  ny_physical_fit fit = ny_physical_fit_of(p, &decision);
  if ((fit.private_fallback && verdict != TK_NEEDS_YOU_VERDICT_LEAVE_IT) ||
      (verdict == TK_NEEDS_YOU_VERDICT_APPROVE && !fit.can_approve)) return;
  if (!tk_needs_you_allows(p, &decision, verdict)) return;
  if (verdict == TK_NEEDS_YOU_VERDICT_APPROVE) {
    size_t n = 0;
    for (; p->title[n] && n + 1 < sizeof mon.echo; n++) mon.echo[n] = p->title[n];
    mon.echo[n] = '\0';
    mon.stage = NY_PAYOFF;
    mon.payoff_until_us = mon.rendered_at_us + 2500LL * 1000LL;
    mon.payoff_provider = p->provider;
    mon.has_payoff_provider = true;
  }
  uint64_t now_ms = mon.rendered_at_us > 0
                        ? (uint64_t)mon.rendered_at_us / 1000u : 0;
  tk_ir_decision_context context;
  bool has_context = tk_ir_policy_capture_visible(
      &mon.interaction_policy, now_ms, &context);
  if (has_context &&
      strncmp(context.request_id, p->request_id, TK_PENDING_ID_CAP) == 0 &&
      mon.tk_needs_you_cb) {
    mon.tk_needs_you_cb(verdict, &context);
  }
  /* Drop it at the tap, not a poll later, so a second tap can not double-answer. */
  tk_needs_you_mark_answered(&mon.needs_you_state, p);
  (void)tk_ir_policy_mark_answered(&mon.interaction_policy, p, now_ms);
  tk_pending_interaction next;
  if (tk_ir_policy_current(&mon.interaction_policy, now_ms, &next)) {
    mon.snapshot.pending = next;
  } else {
    memset(&mon.snapshot.pending, 0, sizeof mon.snapshot.pending);
  }
  render_needs_you();
}

static void needs_you_verdict_event(lv_event_t *event) {
  if (lv_event_get_code(event) != LV_EVENT_CLICKED) return;
  needs_you_resolve(
      (tk_needs_you_verdict)(intptr_t)lv_event_get_user_data(event));
}

/* A tap on the ground: it opens the decision from attract, and on the private
 * screen — where there is nothing readable to decide — it hands the prompt to
 * the terminal. The button screens ignore it; only their buttons resolve. */
static void needs_you_root_event(lv_event_t *event) {
  if (lv_event_get_code(event) != LV_EVENT_CLICKED) return;
  if (mon.stage == NY_ATTRACT) {
    mon.stage = NY_DECISION;
    render_needs_you();
    return;
  }
  const tk_pending_interaction *p = &mon.snapshot.pending;
  tk_needs_you_view decision = tk_needs_you_view_of(&mon.needs_you_state, p);
  ny_physical_fit fit = ny_physical_fit_of(p, &decision);
  if (mon.stage == NY_DECISION && fit.private_fallback) {
    needs_you_resolve(TK_NEEDS_YOU_VERDICT_LEAVE_IT);
  }
}

static lv_obj_t *ny_text(lv_obj_t *parent, const lv_font_t *font,
                         lv_color_t color, int x, int y, int w,
                         int32_t letter_space, lv_text_align_t align) {
  lv_obj_t *object = label(parent, font, color);
  lv_obj_set_pos(object, x, y);
  lv_obj_set_size(object, w, LV_SIZE_CONTENT);
  lv_obj_set_style_text_align(object, align, 0);
  if (letter_space) lv_obj_set_style_text_letter_space(object, letter_space, 0);
  return object;
}

/* A depleting countdown ring: a HAIR track with a Claude arc that drains
 * clockwise from twelve o'clock. Set the drawn fraction with ny_ring_set. */
static lv_obj_t *ny_ring(lv_obj_t *parent, int cx, int cy, int r, int stroke) {
  lv_obj_t *arc = lv_arc_create(parent);
  lv_obj_remove_style_all(arc);
  int diameter = 2 * r + stroke;
  lv_obj_set_size(arc, diameter, diameter);
  lv_obj_set_pos(arc, cx - diameter / 2, cy - diameter / 2);
  lv_arc_set_rotation(arc, 270);
  lv_arc_set_bg_angles(arc, 0, 360);
  lv_obj_set_style_arc_color(arc, COL_HAIR, LV_PART_MAIN);
  lv_obj_set_style_arc_width(arc, stroke, LV_PART_MAIN);
  lv_obj_set_style_arc_color(arc, COL_CLAUDE, LV_PART_INDICATOR);
  lv_obj_set_style_arc_width(arc, stroke, LV_PART_INDICATOR);
  lv_obj_set_style_arc_rounded(arc, true, LV_PART_INDICATOR);
  lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
  return arc;
}

static void ny_ring_set(lv_obj_t *arc, uint16_t permille) {
  uint32_t degrees = ((uint32_t)permille * 360u) / 1000u;
  if (degrees > 360u) degrees = 360u;
  lv_arc_set_angles(arc, 0, degrees);
  mon.render_stats.ring_updates++;
}

static bool ny_ring_update(needs_you_view *view, ny_stage stage,
                           bool private_view, uint16_t permille) {
  if (mon.rendered_ring_valid &&
      mon.rendered_ring_permille == permille) return false;
  lv_obj_t *ring = stage == NY_ATTRACT ? view->a_ring
                   : private_view ? view->pv_ring : view->h_ring;
  ny_ring_set(ring, permille);
  mon.rendered_ring_permille = permille;
  mon.rendered_ring_valid = true;
  return true;
}

/* A touch target: filled slab or outlined pill, ASCII-uppercase label centred,
 * wired to one verdict. Position and size are set by the caller per screen. */
static lv_obj_t *ny_button(lv_obj_t *parent, const char *text,
                           const lv_font_t *font, lv_color_t text_color,
                           lv_color_t accent, bool filled,
                           tk_needs_you_verdict verdict,
                           lv_obj_t **label_out) {
  lv_obj_t *btn = bare(parent);
  lv_obj_set_style_radius(btn, 18, 0);
  if (filled) {
    lv_obj_set_style_bg_color(btn, accent, 0);
    lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, 0);
  } else {
    lv_obj_set_style_bg_opa(btn, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_color(btn, accent, 0);
    lv_obj_set_style_border_opa(btn, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(btn, 2, 0);
  }
  lv_obj_add_flag(btn, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(btn, needs_you_verdict_event, LV_EVENT_CLICKED,
                      (void *)(intptr_t)verdict);
  lv_obj_t *lbl = label(btn, font, text_color);
  lv_label_set_text(lbl, text);
  lv_obj_center(lbl);
  if (label_out) *label_out = lbl;
  return btn;
}

static lv_obj_t *ny_group(lv_obj_t *parent) {
  lv_obj_t *group = bare(parent);
  lv_obj_set_pos(group, 0, 0);
  lv_obj_set_size(group, VP_SCREEN_W, VP_SCREEN_H);
  return group;
}

static lv_obj_t *ny_codex_icon(lv_obj_t *parent, int x, int y) {
  lv_obj_t *icon = lv_image_create(parent);
  lv_image_set_src(icon, &tk_img_codex_64);
  lv_obj_set_pos(icon, x, y);
  lv_obj_remove_flag(icon, LV_OBJ_FLAG_CLICKABLE);
  return icon;
}

static void create_needs_you(lv_obj_t *app_root) {
  needs_you_view *v = &mon.needs_you;
  const lv_text_align_t C = LV_TEXT_ALIGN_CENTER, L = LV_TEXT_ALIGN_LEFT;

  v->root = bare(app_root);
  lv_obj_set_pos(v->root, 0, 0);
  lv_obj_set_size(v->root, VP_SCREEN_W, VP_SCREEN_H);
  lv_obj_set_style_bg_opa(v->root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(v->root, COL_BLACK, 0);
  /* Clickable so ATTRACT opens on a tap and the private screen hands off on a
   * tap; the button screens ignore the ground and only their buttons resolve. */
  lv_obj_add_flag(v->root, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(v->root, needs_you_root_event, LV_EVENT_CLICKED, NULL);

  v->frame = bare(v->root);
  lv_obj_set_pos(v->frame, 14, 14);
  lv_obj_set_size(v->frame, 452, 452);
  lv_obj_set_style_bg_opa(v->frame, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_color(v->frame, COL_CLAUDE, 0);
  lv_obj_set_style_border_opa(v->frame, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(v->frame, 2, 0);
  lv_obj_set_style_radius(v->frame, 40, 0);

  /* -- ATTRACT: the across-the-room alert, ring carrying the countdown ------ */
  v->a_group = ny_group(v->root);
  v->a_ring = ny_ring(v->a_group, 240, 150, 78, 9);
  v->a_mascot = lv_image_create(v->a_group);
  lv_image_set_src(v->a_mascot, &tk_img_mascot_alert_8);
  lv_obj_set_pos(v->a_mascot, 176, 96);
  lv_obj_remove_flag(v->a_mascot, LV_OBJ_FLAG_CLICKABLE);
  v->a_codex_icon = ny_codex_icon(v->a_group, 208, 118);
  v->a_word = ny_text(v->a_group, &plex_headline_48, COL_WHITE, 0, 278, 480, 0, C);
  lv_label_set_text(v->a_word, "NEEDS YOU");
  v->a_project = ny_text(v->a_group, &plex_ui_21, COL_CLAUDE, 0, 332, 480, 3, C);
  v->a_tap = ny_text(v->a_group, &plex_ui_16, COL_DIM, 0, 424, 480, 2, C);
  lv_label_set_text(v->a_tap, "TAP TO ANSWER");

  /* -- Shared decision header: ring + mascot + eyebrow --------------------- */
  v->h_group = ny_group(v->root);
  v->h_ring = ny_ring(v->h_group, 80, 80, 44, 8);
  v->h_mascot = lv_image_create(v->h_group);
  lv_image_set_src(v->h_mascot, &tk_img_mascot_asking_4);
  lv_obj_set_pos(v->h_mascot, 48, 49);
  lv_obj_remove_flag(v->h_mascot, LV_OBJ_FLAG_CLICKABLE);
  v->h_codex_icon = ny_codex_icon(v->h_group, 48, 48);
  /* 14 px keeps CLAUDE NEEDS YOU · PROJECT on one line beside the ring; the
   * design's 15 px has no full-ASCII raster and 16 px wrapped. */
  v->h_eyebrow = ny_text(v->h_group, &plex_ui_14, COL_CLAUDE, 148, 46, 260, 1, L);
  lv_label_set_long_mode(v->h_eyebrow, LV_LABEL_LONG_DOT);

  /* -- QUESTION body ------------------------------------------------------- */
  v->q_group = ny_group(v->root);
  v->q_prompt = ny_text(v->q_group, &plex_body_27, COL_WHITE, 148, 70, 300, 0, L);
  /* Fixed band above the card (y140): two 27px lines. LONG_DOT (not WRAP)
   * ellipsizes instead of overrunning the recommendation card — the render
   * steps the font to 21px first so a long ask stays readable, not clipped.
   * The full question is always in the terminal. */
  lv_obj_set_height(v->q_prompt, 68);
  lv_label_set_long_mode(v->q_prompt, LV_LABEL_LONG_DOT);
  v->q_card = bare(v->q_group);
  lv_obj_set_pos(v->q_card, 24, 140);
  lv_obj_set_size(v->q_card, 432, 92);
  lv_obj_set_style_bg_opa(v->q_card, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_color(v->q_card, COL_HAIR, 0);
  lv_obj_set_style_border_opa(v->q_card, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(v->q_card, 2, 0);
  lv_obj_set_style_radius(v->q_card, 14, 0);
  v->q_rec = ny_text(v->q_card, &plex_ui_14, COL_CLAUDE, 20, 14, 392, 1, L);
  lv_label_set_text(v->q_rec, "CLAUDE RECOMMENDS");
  v->q_title = ny_text(v->q_card, &plex_body_27, COL_WHITE, 20, 34, 392, 0, L);
  lv_obj_set_height(v->q_title, 34);
  v->q_sub = ny_text(v->q_card, &plex_ui_16, COL_MUTED, 20, 70, 392, 0, L);
  lv_obj_set_height(v->q_sub, 20);
  v->q_footer = ny_text(v->q_group, &plex_ui_14, COL_DIM, 0, 440, 480, 1, C);

  /* -- APPROVAL body: the command is the hero, in mono --------------------- */
  v->p_group = ny_group(v->root);
  v->p_desc = ny_text(v->p_group, &plex_body_27, COL_WHITE, 148, 70, 300, 0, L);
  lv_obj_set_height(v->p_desc, 68);
  v->p_chip = bare(v->p_group);
  lv_obj_set_pos(v->p_chip, 24, 146);
  lv_obj_set_size(v->p_chip, 58, 26);
  lv_obj_set_style_bg_opa(v->p_chip, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_color(v->p_chip, COL_HAIR, 0);
  lv_obj_set_style_border_opa(v->p_chip, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(v->p_chip, 2, 0);
  lv_obj_set_style_radius(v->p_chip, 7, 0);
  v->p_chip_lbl = label(v->p_chip, &plex_ui_14, COL_MUTED);
  lv_obj_center(v->p_chip_lbl);
  v->p_cmd = ny_text(v->p_group, &plex_mono_40, COL_WHITE, 24, 182, 432, 0, L);
  lv_obj_set_height(v->p_cmd, 62); /* ends y244: eight clear px before y252 */

  /* -- Shared buttons: APPROVE always filled, DENY the one restrained red -- */
  v->approve = ny_button(v->root, "APPROVE", &plex_attention_25, COL_BLACK,
                         COL_CLAUDE, true, TK_NEEDS_YOU_VERDICT_APPROVE,
                         &v->approve_label);
  v->deny = ny_button(v->root, "DENY", &plex_attention_25, COL_RED, COL_RED,
                      false, TK_NEEDS_YOU_VERDICT_DENY, NULL);
  v->leave = ny_button(v->root, "LEAVE IT", &plex_attention_25, COL_MUTED,
                       COL_DIM, false, TK_NEEDS_YOU_VERDICT_LEAVE_IT, NULL);

  /* -- PRIVATE: no buttons; the mascot holds the secret at 60% ------------- */
  v->pv_group = ny_group(v->root);
  v->pv_ring = ny_ring(v->pv_group, 240, 152, 72, 9);
  v->pv_mascot = lv_image_create(v->pv_group);
  lv_image_set_src(v->pv_mascot, &tk_img_mascot_neutral_7);
  lv_obj_set_pos(v->pv_mascot, 184, 96);
  lv_obj_set_style_image_opa(v->pv_mascot, 153, 0); /* 60%: private reads dim */
  lv_obj_remove_flag(v->pv_mascot, LV_OBJ_FLAG_CLICKABLE);
  v->pv_codex_icon = ny_codex_icon(v->pv_group, 208, 120);
  lv_obj_set_style_image_opa(v->pv_codex_icon, 153, 0);
  v->pv_title = ny_text(v->pv_group, &plex_body_27, COL_WHITE, 0, 274, 480, 1, C);
  lv_label_set_text(v->pv_title, "SOMETHING IS WAITING");
  v->pv_sub = ny_text(v->pv_group, &plex_ui_16, COL_MUTED, 0, 324, 480, 0, C);
  lv_label_set_text(v->pv_sub, "Details stay on the Mac");
  v->pv_tap = ny_text(v->pv_group, &plex_ui_16, COL_DIM, 0, 420, 480, 2, C);
  lv_label_set_text(v->pv_tap, "TAP TO ANSWER AT YOUR DESK");

  /* -- PAYOFF: a static beat, no motion until the motion gate -------------- */
  v->po_group = ny_group(v->root);
  v->po_mascot = lv_image_create(v->po_group);
  lv_image_set_src(v->po_mascot, &tk_img_mascot_happy_8);
  lv_obj_set_pos(v->po_mascot, 176, 120);
  lv_obj_remove_flag(v->po_mascot, LV_OBJ_FLAG_CLICKABLE);
  v->po_codex_icon = ny_codex_icon(v->po_group, 208, 152);
  const int spark[5][3] = {{150, 110, 10}, {322, 96, 12}, {346, 180, 8},
                           {128, 196, 8}, {306, 236, 10}};
  for (int i = 0; i < 5; i++) {
    lv_obj_t *s = bare(v->po_group);
    lv_obj_set_pos(s, spark[i][0], spark[i][1]);
    lv_obj_set_size(s, spark[i][2], spark[i][2]);
    lv_obj_set_style_bg_color(s, COL_CLAUDE, 0);
    lv_obj_set_style_bg_opa(s, LV_OPA_COVER, 0);
    v->po_spark[i] = s;
  }
  /* "ON IT" without the mockup's full stop: headline_48 is uppercase+digits
   * only, and extending that shared font would risk the burn-rate rasters. */
  v->po_word = ny_text(v->po_group, &plex_headline_48, COL_WHITE, 0, 300, 480, 0, C);
  lv_label_set_text(v->po_word, "ON IT");
  v->po_echo = ny_text(v->po_group, &plex_ui_16, COL_MUTED, 0, 358, 480, 0, C);

  lv_obj_add_flag(v->root, LV_OBJ_FLAG_HIDDEN);
}

static void ny_hide_all_groups(needs_you_view *v) {
  ny_show(v->a_group, false);
  ny_show(v->h_group, false);
  ny_show(v->q_group, false);
  ny_show(v->p_group, false);
  ny_show(v->pv_group, false);
  ny_show(v->po_group, false);
  ny_show(v->approve, false);
  ny_show(v->deny, false);
  ny_show(v->leave, false);
}

static void ny_set_provider(needs_you_view *v, bool codex,
                            lv_color_t accent) {
  lv_obj_set_style_border_color(v->frame, accent, 0);
  lv_obj_set_style_arc_color(v->a_ring, accent, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(v->h_ring, accent, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(v->pv_ring, accent, LV_PART_INDICATOR);
  lv_obj_set_style_text_color(v->a_project, accent, 0);
  lv_obj_set_style_text_color(v->h_eyebrow, accent, 0);
  lv_obj_set_style_text_color(v->q_rec, accent, 0);
  lv_obj_set_style_bg_color(v->approve, accent, 0);
  lv_label_set_text(v->q_rec,
                    codex ? "CODEX RECOMMENDS" : "CLAUDE RECOMMENDS");
  lv_label_set_text(v->pv_sub, codex ? "Details stay on your computer"
                                     : "Details stay on the Mac");
  for (int i = 0; i < 5; i++)
    lv_obj_set_style_bg_color(v->po_spark[i], accent, 0);

  ny_show(v->a_mascot, !codex);
  ny_show(v->a_codex_icon, codex);
  ny_show(v->h_mascot, !codex);
  ny_show(v->h_codex_icon, codex);
  ny_show(v->pv_mascot, !codex);
  ny_show(v->pv_codex_icon, codex);
  ny_show(v->po_mascot, !codex);
  ny_show(v->po_codex_icon, codex);
}

/* Paint the stage the policy and the human's tap put us in. Reads
 * tk_needs_you_view_of; decides nothing. Repaints only when what is on the
 * glass actually changes, so ticks between polls are free. */
static void render_needs_you(void) {
  needs_you_view *v = &mon.needs_you;
  const tk_pending_interaction *p = &mon.snapshot.pending;
  int64_t now_us = mon.rendered_at_us;
  tk_agent_provider paint_provider =
      mon.stage == NY_PAYOFF && now_us < mon.payoff_until_us &&
              mon.has_payoff_provider
          ? mon.payoff_provider : p->provider;
  bool codex = paint_provider == TK_AGENT_PROVIDER_CODEX;
  lv_color_t accent = codex ? COL_CODEX : COL_CLAUDE;
  const char *provider = codex ? "CODEX" : "CLAUDE";

  /* PAYOFF is a device-side beat that outlives the answered interaction; it
   * owns the glass for its short static window, then falls back to the page. */
  if (mon.stage == NY_PAYOFF && now_us < mon.payoff_until_us) {
    mon.needs_you_visible = true;
    needs_you_key key;
    memset(&key, 0, sizeof key);
    key.valid = true;
    key.stage = (uint8_t)NY_PAYOFF;
    key.provider = (uint8_t)paint_provider;
    if (!mon.needs_you_rendered.valid ||
        memcmp(&mon.needs_you_rendered, &key, sizeof key) != 0) {
      memcpy(&mon.needs_you_rendered, &key, sizeof key);
      mon.render_stats.full_repaints++;
      mon.rendered_ring_valid = false;
      ny_hide_all_groups(v);
      ny_set_provider(v, codex, accent);
      lv_label_set_text(v->po_echo, mon.echo);
      ny_show(v->po_echo, mon.echo[0] != '\0');
      ny_show(v->po_group, true);
      ny_show(v->root, true);
      lv_obj_move_foreground(v->root);
    } else {
      mon.render_stats.unchanged_ticks++;
    }
    return;
  }
  if (mon.stage == NY_PAYOFF) { /* the static window elapsed */
    mon.stage = NY_ATTRACT;
    mon.stage_id[0] = '\0';
    mon.has_payoff_provider = false;
  }

  tk_needs_you_view decision = tk_needs_you_view_of(&mon.needs_you_state, p);
  ny_physical_fit fit = ny_physical_fit_of(p, &decision);
  mon.needs_you_visible = decision.visible;

  if (!decision.visible) {
    ny_show(v->root, false);
    mon.stage = NY_ATTRACT;
    mon.stage_id[0] = '\0';
    memset(&mon.needs_you_rendered, 0, sizeof mon.needs_you_rendered);
    mon.rendered_ring_valid = false;
    return;
  }

  /* A fresh interaction always begins at the attract stage. */
  if (strncmp(mon.stage_id, p->request_id, TK_PENDING_ID_CAP) != 0) {
    mon.stage = NY_ATTRACT;
    memcpy(mon.stage_id, p->request_id, TK_PENDING_ID_CAP);
    mon.stage_id[TK_PENDING_ID_CAP - 1] = '\0';
  }

  bool is_question = decision.kind == TK_PENDING_QUESTION;
  bool is_private = fit.private_fallback;
  bool offer_approve = decision.offer_approve && fit.can_approve;
  /* DENY is the one restrained-red control: the approval flow only, and only
   * where the service allowed approving — never a blind deny (design law). */
  bool offer_deny = !is_question && offer_approve;

  needs_you_key key;
  memset(&key, 0, sizeof key);
  key.valid = true;
  key.visible = true;
  key.offer_approve = offer_approve;
  key.offer_deny = offer_deny;
  key.marked = p->marked;
  key.has_title = p->has_title;
  key.kind = (uint8_t)decision.kind;
  key.stage = (uint8_t)mon.stage;
  key.options_total = p->options_total;
  key.provider = (uint8_t)p->provider;
  memcpy(key.request_id, p->request_id, sizeof key.request_id);
  if (mon.needs_you_rendered.valid &&
      memcmp(&mon.needs_you_rendered, &key, sizeof key) == 0) {
    if (!ny_ring_update(v, mon.stage, is_private, decision.ring_permille))
      mon.render_stats.unchanged_ticks++;
    return;
  }
  memcpy(&mon.needs_you_rendered, &key, sizeof key);
  mon.render_stats.full_repaints++;
  mon.rendered_ring_valid = false;

  ny_hide_all_groups(v);
  ny_set_provider(v, codex, accent);

  char project[TK_AGENT_PROJECT_CAP];
  tk_agent_monitor_project_label(p->has_project ? p->project : "", project,
                                 sizeof project);

  /* -- ATTRACT ------------------------------------------------------------- */
  if (mon.stage == NY_ATTRACT) {
    ny_ring_update(v, mon.stage, false, decision.ring_permille);
    lv_label_set_text(v->a_project, project);
    ny_show(v->a_project, project[0] != '\0');
    ny_show(v->a_group, true);
    ny_show(v->root, true);
    lv_obj_move_foreground(v->root);
    return;
  }

  /* -- PRIVATE: no buttons; a background tap hands off to the terminal ----- */
  if (is_private) {
    ny_ring_update(v, mon.stage, true, decision.ring_permille);
    ny_show(v->pv_group, true);
    ny_show(v->root, true);
    lv_obj_move_foreground(v->root);
    return;
  }

  /* -- Shared decision header --------------------------------------------- */
  ny_ring_update(v, mon.stage, false, decision.ring_permille);
  lv_image_set_src(v->h_mascot, is_question ? &tk_img_mascot_asking_4
                                            : &tk_img_mascot_neutral_4);
  char eyebrow[80];
  if (project[0])
    snprintf(eyebrow, sizeof eyebrow, "%s NEEDS YOU \xC2\xB7 %s", provider,
             project);
  else
    snprintf(eyebrow, sizeof eyebrow, "%s NEEDS YOU", provider);
  lv_label_set_text(v->h_eyebrow, eyebrow);
  ny_show(v->h_group, true);

  if (is_question) {
    lv_label_set_text(v->approve_label, "APPROVE");
    lv_label_set_text(v->q_prompt, p->has_prompt ? p->prompt : "");
    /* Keep the question inside its 68px band: 27px for a short ask (<=2 lines),
     * else step to 21px so a longer one stays two readable lines above the
     * card instead of overrunning it. LONG_DOT ellipsizes the truly enormous. */
    lv_obj_set_style_text_font(v->q_prompt, fit.prompt_font, 0);
    ny_show(v->q_prompt, p->has_prompt);
    /* The recommendation card exists only when Claude marked an option; an
     * unmarked question arrives here alert-only (can_approve already false). */
    lv_label_set_text(v->q_title, p->title);
    lv_label_set_text(v->q_sub, p->has_subtitle ? p->subtitle : "");
    ny_show(v->q_card, p->marked);
    ny_show(v->q_rec, p->marked);
    ny_show(v->q_title, p->marked);
    ny_show(v->q_sub, p->marked && p->has_subtitle);
    int more = (int)p->options_total - 1;
    if (more >= 1) {
      char footer[48];
      if (codex)
        snprintf(footer, sizeof footer,
                 more == 1 ? "%d MORE OPTION ON COMPUTER"
                           : "%d MORE OPTIONS ON COMPUTER", more);
      else
        snprintf(footer, sizeof footer,
                 more == 1 ? "%d MORE OPTION IN TERMINAL"
                           : "%d MORE OPTIONS IN TERMINAL", more);
      lv_label_set_text(v->q_footer, footer);
    }
    ny_show(v->q_footer, more >= 1);
    ny_show(v->q_group, true);
  } else {
    lv_label_set_text(v->approve_label, codex ? "ALLOW ONCE" : "APPROVE");
    lv_label_set_text(v->p_desc, p->has_subtitle ? p->subtitle : "");
    ny_show(v->p_desc, p->has_subtitle);
    char tool[TK_PENDING_TOOL_CAP];
    ny_ascii_lower(p->has_tool ? p->tool : "", tool, sizeof tool);
    lv_label_set_text(v->p_chip_lbl, tool);
    lv_obj_set_width(v->p_chip, fit.tool_chip_width);
    ny_show(v->p_chip, p->has_tool);
    /* Payload is the hero: mono, verbatim, one stepwise shrink so the longest
     * approvable command still fits at 432 px before truncation would kick in. */
    lv_label_set_text(v->p_cmd, p->title);
    lv_obj_set_style_text_font(v->p_cmd, fit.command_font, 0);
    ny_show(v->p_group, true);
  }

  /* -- Buttons: every target >= 90 px; APPROVE filled and only where allowed */
  int approve_y = is_question ? 244 : 252;
  if (offer_approve) {
    lv_obj_set_pos(v->approve, 24, approve_y);
    lv_obj_set_size(v->approve, 432, 96);
    ny_show(v->approve, true);
  }
  if (offer_deny) {
    int row_y = approve_y + 108;
    lv_obj_set_pos(v->deny, 24, row_y);
    lv_obj_set_size(v->deny, 208, 90);
    ny_show(v->deny, true);
    lv_obj_set_pos(v->leave, 248, row_y);
    lv_obj_set_size(v->leave, 208, 90);
  } else {
    lv_obj_set_pos(v->leave, 24, offer_approve ? approve_y + 106 : approve_y);
    lv_obj_set_size(v->leave, 432, 90);
  }
  ny_show(v->leave, true);

  ny_show(v->root, true);
  lv_obj_move_foreground(v->root);
}

void tk_agent_monitor_set_needs_you_cb(tk_agent_monitor_needs_you_cb cb) {
  mon.tk_needs_you_cb = cb;
}

void tk_agent_monitor_render_stats(tk_agent_render_stats *out) {
  if (out) *out = mon.render_stats;
}

void tk_agent_monitor_render_stats_reset(void) {
  memset(&mon.render_stats, 0, sizeof mon.render_stats);
}

/* Deterministic glass taps for the simulator and host tests. On the device the
 * same paths run from LVGL touch events; these let a headless run drive the
 * two-stage summon without synthesising input. */
void tk_agent_monitor_needs_you_tap(void) {
  if (mon.stage == NY_ATTRACT) {
    mon.stage = NY_DECISION;
    render_needs_you();
  } else {
    const tk_pending_interaction *p = &mon.snapshot.pending;
    tk_needs_you_view decision = tk_needs_you_view_of(&mon.needs_you_state, p);
    ny_physical_fit fit = ny_physical_fit_of(p, &decision);
    if (mon.stage == NY_DECISION && fit.private_fallback) {
      needs_you_resolve(TK_NEEDS_YOU_VERDICT_LEAVE_IT);
    }
  }
}

void tk_agent_monitor_needs_you_press(tk_needs_you_verdict verdict) {
  needs_you_resolve(verdict);
}

void tk_agent_monitor_create(lv_obj_t *app_root) {
  memset(&mon, 0, sizeof mon);
  tk_ir_policy_init(&mon.interaction_policy);
  create_completion(app_root);
  create_needs_you(app_root);
}

static uint64_t monitor_now_ms(int64_t now_us) {
  return now_us > 0 ? (uint64_t)now_us / 1000u : 0;
}

static void select_pending(uint64_t now_ms) {
  tk_pending_interaction selected;
  if (tk_ir_policy_current(&mon.interaction_policy, now_ms, &selected)) {
    mon.snapshot.pending = selected;
  } else {
    memset(&mon.snapshot.pending, 0, sizeof mon.snapshot.pending);
  }
}

static void apply_provider_render(int64_t now_us) {
  uint64_t now_ms = monitor_now_ms(now_us);
  mon.applied_at_us = now_us;
  mon.rendered_at_us = now_us;
  mon.has_snapshot = true;
  tk_completion_queue_apply(&mon.queue, &mon.snapshot, now_ms);
  render_needs_you(); /* it decides whether completion yields the glass */
  render_completion(now_ms);

  const tk_agent_provider_status *providers[2] = {
      &mon.snapshot.claude, &mon.snapshot.codex,
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

void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot,
                            int64_t now_us) {
  if (!snapshot) return;
  mon.snapshot = *snapshot;
  uint64_t now_ms = monitor_now_ms(now_us);
  (void)tk_ir_policy_update_lan(&mon.interaction_policy, &snapshot->pending,
                                now_ms);
  select_pending(now_ms);
  apply_provider_render(now_us);
}

void tk_agent_monitor_apply_status_relay(
    const tk_agent_snapshot *snapshot, int64_t now_us) {
  if (snapshot == NULL) return;
  mon.snapshot.seq = snapshot->seq;
  mon.snapshot.claude = snapshot->claude;
  mon.snapshot.codex = snapshot->codex;
  select_pending(monitor_now_ms(now_us));
  apply_provider_render(now_us);
}

void tk_agent_monitor_apply_relay(const tk_pending_interaction *pending,
                                  int64_t now_us) {
  uint64_t now_ms = monitor_now_ms(now_us);
  (void)tk_ir_policy_update_relay(&mon.interaction_policy, pending, now_ms);
  select_pending(now_ms);
  mon.rendered_at_us = now_us;
  mon.has_snapshot = true;
  render_needs_you();
}

void tk_agent_monitor_tick(int64_t now_us) {
  if (!mon.has_snapshot) return;
  mon.rendered_at_us = now_us;
  select_pending(monitor_now_ms(now_us));
  render_needs_you();
  render_completion(now_us > 0 ? (uint64_t)now_us / 1000ULL : 0);
}

void tk_agent_monitor_dismiss_current(void) {
  tk_completion_queue_dismiss(&mon.queue);
  render_completion(mon.queue.last_now_ms);
}
