#include "torget.h"

#include <string.h>

#include "lvgl.h"

/*
 * Plattformens delade UI: drift-lagret, appväxlingen och launchern som läser
 * appregistret. Ren LVGL 9 — ingen SDL, ingen ESP-IDF, inget nätverk. Byggs
 * byte-identiskt i simulatorn och på targetet.
 *
 * Skärmmodellen: ett drift-lager (480×480) med en root-låda per app plus
 * launcher-overlayn. Exakt en av dem är synlig. Apparna målar fritt i sin
 * root; plattformen rör den aldrig efter create().
 */

extern const lv_font_t plex_text_16;

#define COL_LABEL lv_color_hex(0x8994A5)

static struct {
  lv_obj_t *shift;                 /* driftlådan — allt bor i den */
  lv_obj_t *launcher;
  lv_obj_t *roots[8];              /* en per app; 8 räcker länge (registret är mindre) */
  int active;                      /* index i registret, -1 = launchern uppe */
  int shift_step;
} tg;

/* ---------------------------------------------------------------- helpers */

static lv_obj_t *bare(lv_obj_t *parent) {
  lv_obj_t *o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  /* remove_style_all nollställer INTE default-storleken 100×50 — lådor ska
   * krama sitt innehåll, annars klipps barnen tyst (Solelkollen-läxan). */
  lv_obj_set_size(o, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
  return o;
}

/* ----------------------------------------------------------- appväxlingen */

void torget_app_show(int idx) {
  if (idx < 0 || idx >= torget_app_count) return;
  lv_obj_add_flag(tg.launcher, LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(tg.roots[idx], LV_OBJ_FLAG_HIDDEN);
  tg.active = idx;
  if (torget_apps[idx]->enter) torget_apps[idx]->enter();
}

void torget_app_next(void) {
  torget_app_show(tg.active < 0 ? 0 : (tg.active + 1) % torget_app_count);
}

void torget_launcher_open(void) {
  if (tg.active >= 0) {
    const torget_app_t *app = torget_apps[tg.active];
    if (app->leave) app->leave();
    lv_obj_add_flag(tg.roots[tg.active], LV_OBJ_FLAG_HIDDEN);
    tg.active = -1;
  }
  lv_obj_remove_flag(tg.launcher, LV_OBJ_FLAG_HIDDEN);
}

static void icon_clicked(lv_event_t *e) {
  torget_app_show((int)(intptr_t)lv_event_get_user_data(e));
}

/* -------------------------------------------------------------- launchern */

/* En ikon per app ur registret: platta, glyf, accentprick, namn. Samma
 * proportioner som bänkens ikon (96-platta, radie 22). */
static void launcher_build(void) {
  tg.launcher = bare(tg.shift);
  lv_obj_set_size(tg.launcher, 480, 480);
  lv_obj_set_flex_flow(tg.launcher, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(tg.launcher, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(tg.launcher, 44, 0);
  lv_obj_add_flag(tg.launcher, LV_OBJ_FLAG_HIDDEN);

  for (int i = 0; i < torget_app_count; i++) {
    const torget_app_t *app = torget_apps[i];

    lv_obj_t *cell = bare(tg.launcher);
    lv_obj_set_flex_flow(cell, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(cell, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(cell, 16, 0);

    lv_obj_t *plate = lv_obj_create(cell);
    lv_obj_remove_style_all(plate);
    lv_obj_set_size(plate, 96, 96);
    lv_obj_set_style_radius(plate, 22, 0);
    lv_obj_set_style_bg_opa(plate, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(plate, lv_color_hex(app->icon.plate_hex), 0);
    lv_obj_add_event_cb(plate, icon_clicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);

    lv_obj_t *glyph = lv_label_create(plate);
    lv_obj_set_style_text_font(glyph, app->icon.font, 0);
    lv_obj_set_style_text_color(glyph, lv_color_hex(app->icon.glyph_hex), 0);
    lv_label_set_text(glyph, app->icon.glyph);
    lv_obj_align(glyph, LV_ALIGN_CENTER, -6, 0);

    if (app->icon.dot_hex) {
      lv_obj_t *dot = bare(plate);
      lv_obj_set_size(dot, 12, 12);
      lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
      lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
      lv_obj_set_style_bg_color(dot, lv_color_hex(app->icon.dot_hex), 0);
      lv_obj_align(dot, LV_ALIGN_BOTTOM_RIGHT, -18, -22);
    }

    lv_obj_t *name = lv_label_create(cell);
    lv_obj_set_style_text_font(name, &plex_text_16, 0);
    lv_obj_set_style_text_color(name, COL_LABEL, 0);
    lv_obj_set_style_text_letter_space(name, 2, 0);
    lv_label_set_text(name, app->name);
  }
}

/* ---------------------------------------------------------- pixeldriften */

/* Inbränningsdriften: samma cykel som webbänken, animerad så den aldrig
 * hoppar. Plattformens ansvar — ingen app ska behöva veta att den finns. */
static void drift_anim_x(void *obj, int32_t v) { lv_obj_set_style_translate_x(obj, v, 0); }
static void drift_anim_y(void *obj, int32_t v) { lv_obj_set_style_translate_y(obj, v, 0); }

void torget_drift_step(void) {
  static const int8_t steps[4][2] = { {0, 0}, {2, 1}, {3, -1}, {1, -2} };
  tg.shift_step = (tg.shift_step + 1) % 4;

  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, tg.shift);
  lv_anim_set_duration(&a, 1200);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);

  lv_anim_set_values(&a, lv_obj_get_style_translate_x(tg.shift, 0),
                     steps[tg.shift_step][0]);
  lv_anim_set_exec_cb(&a, drift_anim_x);
  lv_anim_start(&a);

  lv_anim_set_values(&a, lv_obj_get_style_translate_y(tg.shift, 0),
                     steps[tg.shift_step][1]);
  lv_anim_set_exec_cb(&a, drift_anim_y);
  lv_anim_start(&a);
}

static void drift_timer(lv_timer_t *t) {
  (void)t;
  torget_drift_step();
}

/* ------------------------------------------------------------------ bygge */

void torget_ui_create(void) {
  memset(&tg, 0, sizeof tg);
  tg.active = -1;

  lv_obj_t *scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

  tg.shift = bare(scr);
  lv_obj_set_size(tg.shift, 480, 480);

  for (int i = 0; i < torget_app_count; i++) {
    const torget_app_t *app = torget_apps[i];
    /* Fel kontraktsversion: appen får ingen root och syns aldrig. Ett hål i
     * launchern är ärligare än en krasch halvvägs in i en främmande create(). */
    if (app->api_version != TORGET_APP_API_VERSION) {
      LV_LOG_WARN("app %s byggd mot kontraktsversion %d, plattformen har %d — hoppas över",
                  app->name, app->api_version, TORGET_APP_API_VERSION);
      continue;
    }
    tg.roots[i] = bare(tg.shift);
    lv_obj_set_size(tg.roots[i], 480, 480);
    lv_obj_add_flag(tg.roots[i], LV_OBJ_FLAG_HIDDEN);
    app->create(tg.roots[i]);
  }

  launcher_build();
  lv_timer_create(drift_timer, 60000, NULL);

  /* Boota rakt in i första appen — skärmen på hyllan ska visa data, inte en
   * meny. Launchern är ett långtryck bort. */
  torget_app_show(0);
}
