#include "round_diagnostic.h"
#include <stdio.h>
#include <stdint.h>

extern const lv_font_t plex_ui_16;

static void count_touch(lv_event_t *event) {
    lv_obj_t *button = lv_event_get_target_obj(event);
    unsigned count = (unsigned)(uintptr_t)lv_obj_get_user_data(button) + 1;
    lv_obj_set_user_data(button, (void *)(uintptr_t)count);
    lv_obj_t *value = lv_obj_get_child(button, 1);
    lv_label_set_text_fmt(value, "%u", count);
}

static lv_obj_t *text(lv_obj_t *parent, const char *value, int y) {
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_style_text_font(label, &plex_ui_16, 0);
    lv_obj_set_style_text_color(label, lv_color_white(), 0);
    lv_obj_set_width(label, 280);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(label, 93, y);
    lv_label_set_text(label, value);
    return label;
}

lv_obj_t *tg_round_diagnostic_create(lv_obj_t *parent) {
    lv_obj_t *root = lv_obj_create(parent);
    lv_obj_remove_style_all(root);
    lv_obj_set_size(root, 466, 466);
    lv_obj_set_style_bg_color(root, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
    lv_obj_remove_flag(root, LV_OBJ_FLAG_SCROLLABLE);
    text(root, "1.75 ROUND · TOUCH TEST", 127);
    text(root, "TAP ALL FIVE TARGETS", 315);
    static const int xy[5][2] = {{233, 66}, {400, 233}, {233, 400}, {66, 233}, {233, 233}};
    static const char *const names[] = {"N", "E", "S", "W", "C"};
    for (int i = 0; i < 5; ++i) {
        lv_obj_t *button = lv_button_create(root);
        lv_obj_remove_style_all(button);
        lv_obj_set_pos(button, xy[i][0] - 28, xy[i][1] - 28);
        lv_obj_set_size(button, 56, 56);
        lv_obj_set_style_radius(button, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(button, lv_color_hex(0x25272d), 0);
        lv_obj_set_style_bg_opa(button, LV_OPA_COVER, 0);
        lv_obj_set_style_text_color(button, lv_color_white(), 0);
        lv_obj_set_style_text_font(button, &plex_ui_16, 0);
        lv_obj_t *name = lv_label_create(button);
        lv_label_set_text(name, names[i]);
        lv_obj_align(name, LV_ALIGN_TOP_MID, 0, 5);
        lv_obj_t *value = lv_label_create(button);
        lv_label_set_text(value, "0");
        lv_obj_align(value, LV_ALIGN_BOTTOM_MID, 0, -5);
        lv_obj_add_event_cb(button, count_touch, LV_EVENT_CLICKED, NULL);
    }
    static const uint32_t colors[] = {0xff0000, 0x00ff00, 0x0000ff, 0xD97757, 0x6F78FF};
    for (int i = 0; i < 5; ++i) {
        lv_obj_t *swatch = lv_obj_create(root);
        lv_obj_remove_style_all(swatch);
        lv_obj_set_pos(swatch, 151 + 34 * i, 167);
        lv_obj_set_size(swatch, 28, 14);
        lv_obj_set_style_bg_color(swatch, lv_color_hex(colors[i]), 0);
        lv_obj_set_style_bg_opa(swatch, LV_OPA_COVER, 0);
    }
    return root;
}
