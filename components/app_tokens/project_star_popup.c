#include "project_star_popup.h"

#include <stdio.h>
#include <string.h>

#include "project_star_assets.h"
#include "project_star_popup_policy.h"
#include "project_star_style.h"
#include "board_layout.h"

extern const lv_font_t plex_num_38;
extern const lv_font_t plex_ui_21;
extern const lv_font_t plex_ui_16;
extern const lv_font_t plex_ui_14;

#define COL_BLACK lv_color_hex(VP_COLOR_BACKGROUND)
#define COL_WHITE lv_color_hex(VP_COLOR_TEXT)
#define COL_MUTED lv_color_hex(VP_COLOR_MUTED)
#define COL_REPO lv_color_hex(TK_PROJECT_STAR_REPO_COLOR_HEX)
#define COL_STAR lv_color_hex(TK_PROJECT_STAR_COLOR_HEX)
#define COL_STAR_EDGE lv_color_hex(TK_PROJECT_STAR_EDGE_COLOR_HEX)

/* The approved star is drawn as ten tiny triangles directly into LVGL's
 * current layer. That gives us a filled silhouette without a canvas, bitmap
 * scaling, transformed layer, or persistent full-screen buffer. */
static const lv_point_t STAR_POINTS_220[] = {
    {110, 7}, {142, 71}, {213, 82}, {161, 132}, {174, 203},
    {110, 169}, {46, 203}, {59, 132}, {7, 82}, {78, 71},
};

static struct {
  lv_obj_t *root;
  lv_obj_t *repo;
  lv_obj_t *actor;
  lv_obj_t *count;
  lv_obj_t *dismiss;
  tk_project_star_popup_policy policy;
} popup;

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *object = lv_obj_create(parent);
  lv_obj_remove_style_all(object);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font,
                       lv_color_t color, int x, int y, int width, int height) {
  lv_obj_t *object = lv_label_create(parent);
  lv_obj_set_style_text_font(object, font, 0);
  lv_obj_set_style_text_color(object, color, 0);
  lv_obj_set_pos(object, x, y);
  lv_obj_set_size(object, width, height);
  lv_label_set_long_mode(object, LV_LABEL_LONG_CLIP);
  lv_obj_set_style_text_align(object, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
  return object;
}

static void star_draw(lv_event_t *event) {
  if (lv_event_get_code(event) != LV_EVENT_DRAW_MAIN) return;

  lv_obj_t *object = lv_event_get_current_target(event);
  lv_layer_t *layer = lv_event_get_layer(event);
  lv_area_t area;
  lv_obj_get_coords(object, &area);
  const int32_t width = lv_obj_get_width(object);
  const int32_t height = lv_obj_get_height(object);
  const int32_t center_x = area.x1 + width / 2;
  const int32_t center_y = area.y1 + height / 2;
  lv_point_precise_t points[10];

  for (int i = 0; i < 10; i++) {
    points[i].x = area.x1 + STAR_POINTS_220[i].x * width / 220;
    points[i].y = area.y1 + STAR_POINTS_220[i].y * height / 220;
  }

  lv_draw_triangle_dsc_t fill;
  lv_draw_triangle_dsc_init(&fill);
  fill.base.layer = layer;
  fill.color = COL_STAR;
  fill.opa = LV_OPA_COVER;
  for (int i = 0; i < 10; i++) {
    fill.p[0].x = center_x;
    fill.p[0].y = center_y;
    fill.p[1] = points[i];
    fill.p[2] = points[(i + 1) % 10];
    lv_draw_triangle(layer, &fill);
  }

  lv_draw_line_dsc_t edge;
  lv_draw_line_dsc_init(&edge);
  edge.base.layer = layer;
  edge.color = COL_STAR_EDGE;
  edge.opa = LV_OPA_COVER;
  edge.width = width >= 100 ? 3 : 2;
  edge.round_start = 1;
  edge.round_end = 1;
  for (int i = 0; i < 10; i++) {
    edge.p1 = points[i];
    edge.p2 = points[(i + 1) % 10];
    lv_draw_line(layer, &edge);
  }
}

static lv_obj_t *filled_star(lv_obj_t *parent, int x, int y,
                             int width, int height) {
  lv_obj_t *object = bare(parent);
  lv_obj_set_pos(object, x, y);
  lv_obj_set_size(object, width, height);
  lv_obj_add_event_cb(object, star_draw, LV_EVENT_DRAW_MAIN, NULL);
  return object;
}

static void popup_event(lv_event_t *event) {
  if (lv_event_get_code(event) != LV_EVENT_CLICKED) return;
  if (tk_project_star_popup_policy_dismiss(&popup.policy)) {
    lv_obj_add_flag(popup.root, LV_OBJ_FLAG_HIDDEN);
  }
}

void tk_project_star_popup_create(lv_obj_t *app_root) {
  memset(&popup, 0, sizeof popup);
  tk_project_star_popup_policy_init(&popup.policy);
  popup.root = bare(app_root);
  lv_obj_set_pos(popup.root, 0, 0);
  lv_obj_set_size(popup.root, VP_SCREEN_W, VP_SCREEN_H);
  lv_obj_set_style_bg_opa(popup.root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(popup.root, COL_BLACK, 0);
  /* Intercept input while visible so the underlying tile remains exactly
   * where it was. The whole quiet display state dismisses with one tap. */
  lv_obj_add_flag(popup.root, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(popup.root, popup_event, LV_EVENT_CLICKED, NULL);

  lv_obj_t *github_mark = lv_image_create(popup.root);
  lv_image_set_src(github_mark, &tk_img_github_mark_24);
  lv_obj_set_pos(github_mark, 20, 20);
  lv_obj_remove_flag(github_mark, LV_OBJ_FLAG_CLICKABLE);
#ifdef TORGET_BOARD_175
  lv_obj_add_flag(github_mark, LV_OBJ_FLAG_HIDDEN);
#endif

  popup.repo = label(popup.root, &plex_ui_16, COL_REPO,
                     55, 24, 405, 24);
  lv_obj_set_style_text_align(popup.repo, LV_TEXT_ALIGN_LEFT, 0);

  /* Final state of the impact: one large, still, fully filled gold star. */
#ifdef TORGET_BOARD_191_TOUCH
  filled_star(popup.root, 30, 66, 130, 130);
#else
  filled_star(popup.root, 130, 80, 220, 220);
#endif

  popup.actor = label(popup.root, &plex_ui_21, COL_WHITE,
                      20, 327, 440, 30);

#ifndef TORGET_BOARD_191_TOUCH
  filled_star(popup.root, 145, 380, 40, 40);
#endif
  popup.count = label(popup.root, &plex_num_38, COL_WHITE,
                      200, 380, 240, 46);
  lv_obj_set_style_text_align(popup.count, LV_TEXT_ALIGN_LEFT, 0);

  popup.dismiss = label(popup.root, &plex_ui_14, COL_MUTED,
                        20, 442, 440, 20);
  lv_obj_set_style_text_letter_space(popup.dismiss, 2, 0);
  lv_label_set_text(popup.dismiss, "TAP TO DISMISS");
#ifdef TORGET_BOARD_191_TOUCH
  lv_obj_set_pos(popup.actor, 175, 85); lv_obj_set_width(popup.actor, 280);
  lv_obj_set_pos(popup.count, 200, 133); lv_obj_set_width(popup.count, 240);
  lv_obj_set_y(popup.dismiss, 212);
#endif

#ifdef TORGET_BOARD_175
  lv_obj_set_pos(popup.repo, 79, 67);
  lv_obj_set_width(popup.repo, 322);
  lv_obj_set_style_text_align(popup.repo, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_pos(popup.actor, 90, 323);
  lv_obj_set_width(popup.actor, 300);
  lv_obj_set_style_text_align(popup.actor, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_pos(popup.count, 190, 369);
  lv_obj_set_width(popup.count, 180);
  lv_obj_set_pos(popup.dismiss, 110, 414);
  lv_obj_set_width(popup.dismiss, 260);
  lv_obj_set_style_text_align(popup.dismiss, LV_TEXT_ALIGN_CENTER, 0);
#endif

  lv_obj_add_flag(popup.root, LV_OBJ_FLAG_HIDDEN);
}

bool tk_project_star_popup_show(const tk_project_star_event *event,
                               int64_t now_us) {
  if (!event || !event->id[0] ||
      !tk_project_star_popup_policy_show(&popup.policy, event->id, now_us)) {
    return false;
  }

  lv_label_set_text(popup.repo,
                    event->repo[0] ? event->repo : event->project);

  char actor[TK_GITHUB_ACTOR_CAP + 2];
  if (event->has_actor)
    snprintf(actor, sizeof actor, "@%s", event->actor);
  else
    snprintf(actor, sizeof actor, "Someone");
  lv_label_set_text(popup.actor, actor);

  char count[32];
  snprintf(count, sizeof count, "%ld stars", (long)event->stars);
  lv_label_set_text(popup.count, count);

  lv_obj_remove_flag(popup.root, LV_OBJ_FLAG_HIDDEN);
  /* Do not move this object to the global top layer. The agent monitor is
   * created after it and remains the higher-priority transient state. */
  return true;
}

void tk_project_star_popup_tick(int64_t now_us) {
  if (tk_project_star_popup_policy_tick(&popup.policy, now_us)) {
    lv_obj_add_flag(popup.root, LV_OBJ_FLAG_HIDDEN);
  }
}

bool tk_project_star_popup_visible(void) {
  return popup.root && popup.policy.visible &&
         !lv_obj_has_flag(popup.root, LV_OBJ_FLAG_HIDDEN);
}
