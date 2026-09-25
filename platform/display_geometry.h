#ifndef TORGET_DISPLAY_GEOMETRY_H
#define TORGET_DISPLAY_GEOMETRY_H

#include "lvgl.h"

/* Keep the existing app contract and native-size fonts/assets. The 2.41
 * landscape display centres the 480 px composition, reducing the empty
 * vertical margins by 15 px. Controls and borders stay inside the glass. */
#ifdef TORGET_BOARD_241_V2
#define TG_DISPLAY_WIDTH 600
#define TG_DISPLAY_HEIGHT 450
#define TG_VIEWPORT_X 60
#define TG_VIEWPORT_Y (-15)
#define TG_VIEWPORT_INSET_Y 15
#define TG_NEEDS_FRAME_INSET_Y 6
#define TG_FOOTER_LIFT 8
#define TG_DISPLAY_FLUSH_ROWS 8
#define TG_SETTINGS_GPIO 0
#define TG_SETTINGS_CLOSE_TEXT "BOOT CLOSES"
#define TG_SETTINGS_HOLD_TEXT "HOLD BOOT FOR SETUP"
#elif defined(TORGET_BOARD_18_V2)
#define TG_DISPLAY_WIDTH 368
#define TG_DISPLAY_HEIGHT 448
/* Keep the existing 480 px authored composition inside the native glass while
 * board-specific reflow is developed. LVGL applies this to each full-screen
 * viewport, with its pivot at the origin; the simulator must review every
 * surface and the target must budget the temporary transform layer. */
#define TG_VIEWPORT_X 0
#define TG_VIEWPORT_Y 0
#define TG_VIEWPORT_SCALE_X 196
#define TG_VIEWPORT_SCALE_Y 239
#define TG_VIEWPORT_INSET_Y 0
#define TG_NEEDS_FRAME_INSET_Y 0
#define TG_FOOTER_LIFT 0
#define TG_DISPLAY_FLUSH_ROWS 12
#define TG_SETTINGS_GPIO 0
#define TG_SETTINGS_CLOSE_TEXT "BOOT CLOSES"
#define TG_SETTINGS_HOLD_TEXT "HOLD BOOT FOR SETUP"
#else
#define TG_DISPLAY_WIDTH 480
#define TG_DISPLAY_HEIGHT 480
#define TG_VIEWPORT_X 0
#define TG_VIEWPORT_Y 0
#define TG_VIEWPORT_SCALE_X 256
#define TG_VIEWPORT_SCALE_Y 256
#define TG_VIEWPORT_INSET_Y 0
#define TG_NEEDS_FRAME_INSET_Y 0
#define TG_FOOTER_LIFT 0
#define TG_DISPLAY_FLUSH_ROWS 12
#define TG_SETTINGS_GPIO 18
#define TG_SETTINGS_CLOSE_TEXT "KEY3 CLOSES"
#define TG_SETTINGS_HOLD_TEXT "HOLD KEY3 FOR SETUP"
#endif

static inline void tg_position_viewport(lv_obj_t *object) {
    lv_obj_set_size(object, 480, 480);
    lv_obj_set_pos(object, TG_VIEWPORT_X, TG_VIEWPORT_Y);
#if defined(TORGET_BOARD_18_V2)
    lv_obj_set_style_transform_pivot_x(object, 0, 0);
    lv_obj_set_style_transform_pivot_y(object, 0, 0);
    lv_obj_set_style_transform_scale_x(object, TG_VIEWPORT_SCALE_X, 0);
    lv_obj_set_style_transform_scale_y(object, TG_VIEWPORT_SCALE_Y, 0);
#endif
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
}

#endif
