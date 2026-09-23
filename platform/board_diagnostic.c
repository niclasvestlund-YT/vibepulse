#include "board_diagnostic.h"
#include "display_geometry.h"
#include "lvgl.h"
#include <stdio.h>
extern const lv_font_t plex_ui_21;
extern const lv_font_t plex_ui_16;
static unsigned counts[4];
static lv_obj_t *labels[4];
static void corner(lv_event_t *event) {
    unsigned i = (unsigned)(uintptr_t)lv_event_get_user_data(event);
    counts[i]++;
    lv_label_set_text_fmt(labels[i], "%u: %u", i + 1, counts[i]);
    printf("191 touch corner %u count %u\n", i + 1, counts[i]);
}
void tg_board_diagnostic_create(void) {
    lv_obj_t *screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title = lv_label_create(screen);
    lv_obj_set_style_text_font(title, &plex_ui_21, 0);
    lv_obj_set_style_text_color(title, lv_color_white(), 0);
    lv_label_set_text(title, "VIBEPULSE 1.91  /  536 x 240");
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 75);
    const uint32_t colors[] = {0xFF0000, 0x00FF00, 0x0000FF, 0xD97757, 0x6F78FF};
    for (int i = 0; i < 5; ++i) {
        lv_obj_t *swatch = lv_obj_create(screen);
        lv_obj_remove_style_all(swatch);
        lv_obj_set_pos(swatch, 108 + i * 65, 114);
        lv_obj_set_size(swatch, 60, 28);
        lv_obj_set_style_bg_color(swatch, lv_color_hex(colors[i]), 0);
        lv_obj_set_style_bg_opa(swatch, LV_OPA_COVER, 0);
    }
    lv_obj_t *hint = lv_label_create(screen);
    lv_obj_set_style_text_font(hint, &plex_ui_16, 0);
    lv_obj_set_style_text_color(hint, lv_color_hex(0x9298A2), 0);
    lv_label_set_text(hint, "TAP ALL FOUR CORNERS");
    lv_obj_align(hint, LV_ALIGN_TOP_MID, 0, 154);
    for (unsigned i = 0; i < 4; ++i) {
        lv_obj_t *button = lv_obj_create(screen);
        lv_obj_remove_style_all(button);
        lv_obj_set_size(button, 92, 54);
        lv_obj_set_pos(button, (i % 2) ? TG_DISPLAY_WIDTH - 106 : 14,
                              (i / 2) ? TG_DISPLAY_HEIGHT - 66 : 12);
        lv_obj_set_style_border_color(button, lv_color_hex(0x9298A2), 0);
        lv_obj_set_style_border_width(button, 2, 0);
        lv_obj_set_style_radius(button, 12, 0);
        lv_obj_add_flag(button, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(button, corner, LV_EVENT_CLICKED, (void *)(uintptr_t)i);
        labels[i] = lv_label_create(button);
        lv_obj_set_style_text_font(labels[i], &plex_ui_21, 0);
        lv_obj_set_style_text_color(labels[i], lv_color_white(), 0);
        lv_label_set_text_fmt(labels[i], "%u: 0", i + 1);
        lv_obj_center(labels[i]);
    }
}
