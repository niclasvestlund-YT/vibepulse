#pragma once
#include "lvgl.h"
void grok_ui_create(lv_obj_t *screen);
void grok_ui_set_action(void (*action)(void));
void grok_ui_voice_update(const char *state, const char *transcript, const char *reply);
