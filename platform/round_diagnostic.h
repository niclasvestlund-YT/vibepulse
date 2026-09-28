#pragma once
#include "lvgl.h"

/* Source-shared diagnostic; simulator clicks do not certify physical touch. */
lv_obj_t *tg_round_diagnostic_create(lv_obj_t *parent);
