#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "esp_lv_adapter.h"
#include "grok_ui.h"
#include "voice_device.h"
#include "esp_lv_adapter_input.h"

#if CONFIG_GROK_TOUCH_DIAGNOSTICS
static esp_err_t touch_read_debug(esp_lcd_touch_handle_t tp,
        esp_lcd_touch_point_data_t *points, uint8_t *count,
        uint8_t max_count, void *ctx) {
    (void)ctx;
    static unsigned scans;
    esp_err_t err = esp_lcd_touch_read_data(tp);
    if (err == ESP_OK) err = esp_lcd_touch_get_data(tp, points, count, max_count);
    if ((scans++ % 150) == 0 || *count) {
        ESP_LOGI("grok_touch", "scan=%u err=%s count=%u x=%u y=%u",
            scans, esp_err_to_name(err), *count,
            *count ? points[0].x : 0, *count ? points[0].y : 0);
    }
    return err;
}

static void touch_pressed(lv_event_t *event) {
    (void)event;
    lv_point_t point;
    lv_indev_get_point(bsp_display_get_input_dev(), &point);
    ESP_LOGI("grok_touch", "press x=%ld y=%ld", (long)point.x, (long)point.y);
}
#endif

void app_main(void) {
    bsp_display_cfg_t cfg = {
        .lv_adapter_cfg = ESP_LV_ADAPTER_DEFAULT_CONFIG(),
        /* Vendor BSP default: physical 7B mounting uses 180 degrees with
         * GT911 mirror_x/mirror_y enabled. Owner confirmed 0 was inverted. */
        .rotation = ESP_LV_ADAPTER_ROTATE_180,
        .tear_avoid_mode = ESP_LV_ADAPTER_TEAR_AVOID_MODE_TRIPLE_PARTIAL,
        .touch_flags = { .swap_xy = 0, .mirror_x = 1, .mirror_y = 1 },
    };
    lv_display_t *display = bsp_display_start_with_config(&cfg);
    ESP_ERROR_CHECK(display ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(esp_lv_adapter_lock(-1));
    grok_ui_create(lv_display_get_screen_active(display));
    grok_ui_set_action(voice_device_request);
#if CONFIG_GROK_TOUCH_DIAGNOSTICS
    const esp_lv_adapter_touch_callbacks_t touch_callbacks = {
        .custom_touch_read = touch_read_debug,
    };
    ESP_ERROR_CHECK(esp_lv_adapter_set_touch_callbacks(bsp_display_get_input_dev(), &touch_callbacks));
    lv_indev_add_event_cb(bsp_display_get_input_dev(), touch_pressed, LV_EVENT_PRESSED, NULL);
#endif
    esp_lv_adapter_unlock();
    bsp_display_brightness_set(45);
    voice_device_init();
    ESP_LOGI("grok", "7B boot: display=%dx%d internal=%u largest_dma=%u psram=%u",
        (int)lv_display_get_horizontal_resolution(display),
        (int)lv_display_get_vertical_resolution(display),
        (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
        (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
        (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}
