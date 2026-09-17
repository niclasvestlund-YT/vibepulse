#pragma once
#include <stddef.h>
#include "esp_err.h"
#include "esp_lcd_types.h"
#include "esp_lcd_touch.h"
#include "display_geometry.h"
#ifndef TORGET_BOARD_241_V2
#include "bsp/esp-bsp.h"
#endif

esp_err_t tg_board_display_new(size_t transfer_size,
    esp_lcd_panel_handle_t *panel, esp_lcd_panel_io_handle_t *io);
esp_err_t tg_board_touch_new(esp_lcd_touch_handle_t *touch);
esp_err_t tg_board_brightness_init(void);
esp_err_t tg_board_brightness_set(int percent);
