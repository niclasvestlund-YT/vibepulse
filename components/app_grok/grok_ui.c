#include "grok_ui.h"
#include <stdio.h>
#include <string.h>

LV_FONT_DECLARE(grok_inter_18);
LV_FONT_DECLARE(grok_inter_24);
LV_FONT_DECLARE(grok_inter_28);
LV_FONT_DECLARE(grok_inter_46);
LV_IMAGE_DECLARE(grok_avatar);

static lv_obj_t *status_label;
static lv_obj_t *diagnostics;
static unsigned touch_count;
static lv_obj_t *transcript_label, *reply_label, *talk_button, *talk_label;
static void (*talk_action)(void);

void grok_ui_set_action(void (*action)(void)) { talk_action = action; }

void grok_ui_voice_update(const char *state, const char *transcript, const char *reply) {
    if (!status_label) return;
    lv_label_set_text(status_label, state);
    lv_label_set_text(transcript_label, transcript);
    lv_label_set_text(reply_label, reply);
    bool busy = !strcmp(state, "Listening") || !strcmp(state, "Thinking") || !strcmp(state, "Speaking");
    if (busy) lv_obj_add_state(talk_button, LV_STATE_DISABLED);
    else lv_obj_remove_state(talk_button, LV_STATE_DISABLED);
    lv_label_set_text(talk_label, busy ? state : "Ask my bot team");
}

static lv_obj_t *text(lv_obj_t *parent, const char *value, int x, int y,
                      const lv_font_t *font, uint32_t color) {
    lv_obj_t *o = lv_label_create(parent);
    lv_obj_set_style_text_font(o, font, 0);
    lv_obj_set_style_text_color(o, lv_color_hex(color), 0);
    lv_label_set_text(o, value);
    lv_obj_set_pos(o, x, y);
    return o;
}

static void open_bot(lv_event_t *event) {
    (void)event;
    if (talk_action) talk_action();
    else lv_label_set_text(status_label, "Voice bridge not configured");
}

static void corner_pressed(lv_event_t *event) {
    lv_obj_t *button = lv_event_get_target_obj(event);
    lv_obj_set_style_bg_color(button, lv_color_hex(0x335840), 0);
    ++touch_count;
    char value[80];
    snprintf(value, sizeof(value), "Touch received: %u", touch_count);
    lv_label_set_text(lv_obj_get_child(button, 0), value);
}

static void close_diagnostics(lv_event_t *event) {
    (void)event;
    lv_obj_delete(diagnostics);
    diagnostics = NULL;
}

static void show_diagnostics(lv_event_t *event) {
    (void)event;
    if (diagnostics) return;
    diagnostics = lv_obj_create(lv_screen_active());
    lv_obj_remove_style_all(diagnostics);
    lv_obj_set_size(diagnostics, 1024, 600);
    lv_obj_set_style_bg_color(diagnostics, lv_color_hex(0x090909), 0);
    lv_obj_set_style_bg_opa(diagnostics, LV_OPA_COVER, 0);
    text(diagnostics, "Touch check", 400, 210, &grok_inter_28, 0xffffff);
    text(diagnostics, "Tap each corner. All four should respond.", 282, 258,
         &grok_inter_18, 0xb5b5b5);
    const int xy[4][2] = {{20,20},{784,20},{20,490},{784,490}};
    const char *names[] = {"Top left", "Top right", "Bottom left", "Bottom right"};
    for (int i=0; i<4; ++i) {
        lv_obj_t *b = lv_button_create(diagnostics);
        lv_obj_set_pos(b, xy[i][0], xy[i][1]);
        lv_obj_set_size(b, 220, 90);
        lv_obj_set_style_bg_color(b, lv_color_hex(0x292929), 0);
        lv_obj_t *label = text(b, names[i], 0, 0, &grok_inter_18, 0xffffff);
        lv_obj_center(label);
        lv_obj_add_event_cb(b, corner_pressed, LV_EVENT_CLICKED, NULL);
    }
    lv_obj_t *done = lv_button_create(diagnostics);
    lv_obj_set_size(done, 150, 60);
    lv_obj_set_pos(done, 437, 315);
    lv_obj_set_style_bg_color(done, lv_color_hex(0xeeeeee), 0);
    lv_obj_center(text(done, "Done", 0, 0, &grok_inter_24, 0x090909));
    lv_obj_add_event_cb(done, close_diagnostics, LV_EVENT_CLICKED, NULL);
}

void grok_ui_create(lv_obj_t *screen) {
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x090909), 0);
    text(screen, "Grok Bot", 42, 26, &grok_inter_28, 0xf5f5f5);
    text(screen, "Voice", 850, 32, &grok_inter_18, 0xaaaaaa);
    lv_obj_t *settings = lv_button_create(screen);
    lv_obj_remove_style_all(settings);
    lv_obj_set_size(settings, 60, 60);
    lv_obj_set_pos(settings, 934, 14);
    lv_obj_center(text(settings, LV_SYMBOL_SETTINGS, 0, 0,
                       &lv_font_montserrat_28, 0xeeeeee));
    lv_obj_add_event_cb(settings, show_diagnostics, LV_EVENT_CLICKED, NULL);

    /* Circular viewport removes the raster asset's rectangular backing without
     * replacing the approved artwork with procedurally drawn facial features. */
    lv_obj_t *avatar_frame = lv_obj_create(screen);
    lv_obj_remove_style_all(avatar_frame);
    lv_obj_set_pos(avatar_frame, 108, 118);
    lv_obj_set_size(avatar_frame, 352, 352);
    lv_obj_set_style_radius(avatar_frame, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_clip_corner(avatar_frame, true, 0);
    lv_obj_remove_flag(avatar_frame, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *avatar = lv_image_create(avatar_frame);
    lv_image_set_src(avatar, &grok_avatar);
    lv_obj_set_pos(avatar, -47, -47);
    text(screen, "Grok", 550, 140, &grok_inter_46, 0xf5f5f5);
    status_label = text(screen, "Tap to talk", 552, 216,
                        &grok_inter_18, 0xb5b5b5);
    lv_obj_t *open = lv_button_create(screen);
    talk_button = open;
    lv_obj_set_pos(open, 550, 259);
    lv_obj_set_size(open, 380, 84);
    lv_obj_set_style_radius(open, 42, 0);
    lv_obj_set_style_bg_color(open, lv_color_hex(0xf4f4f4), 0);
    lv_obj_set_style_shadow_width(open, 0, 0);
    talk_label = text(open, "Ask my bot team", 0, 0, &grok_inter_24, 0x090909);
    lv_obj_center(talk_label);
    lv_obj_add_event_cb(open, open_bot, LV_EVENT_CLICKED, NULL);
    transcript_label = text(screen, "", 552, 359, &grok_inter_18, 0xaaaaaa);
    lv_obj_set_size(transcript_label, 395, 54);
    lv_label_set_long_mode(transcript_label, LV_LABEL_LONG_MODE_DOTS);
    reply_label = text(screen, "", 552, 422, &grok_inter_18, 0xeeeeee);
    lv_obj_set_size(reply_label, 395, 80);
    lv_label_set_long_mode(reply_label, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_t *divider = lv_obj_create(screen);
    lv_obj_remove_style_all(divider);
    lv_obj_set_pos(divider, 0, 522);
    lv_obj_set_size(divider, 1024, 1);
    lv_obj_set_style_bg_color(divider, lv_color_hex(0x292929), 0);
    lv_obj_set_style_bg_opa(divider, LV_OPA_COVER, 0);
    text(screen, "Grok usage", 42, 551, &grok_inter_18, 0xf0f0f0);
    text(screen, "-   Unavailable", 170, 551, &grok_inter_18, 0x999999);
}
