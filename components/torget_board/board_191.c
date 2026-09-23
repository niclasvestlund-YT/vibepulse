#include "torget_board.h"
#ifdef TORGET_BOARD_191_TOUCH
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "freertos/task.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_sh8601.h"
#include "esp_lcd_touch_ft5x06.h"

/* Waveshare ESP32-S3-Touch-AMOLED-1.91, vendor 1986f380a4e6.
 * RM67162 uses the vendor's SH8601 QSPI transport. Native touch is portrait:
 * mirror raw X then swap XY to match landscape MADCTL 0xF0.
 * GPIO18 is LCD DATA0, never a settings button on this board. */
static const char *TAG = "board-191";
static i2c_master_bus_handle_t bus;
static esp_lcd_panel_io_handle_t panel_io;
static esp_err_t (*touch_read_original)(esp_lcd_touch_handle_t);
static esp_err_t touch_read_diagnosed(esp_lcd_touch_handle_t touch) {
    static bool reported;
    const TickType_t started = xTaskGetTickCount();
    esp_err_t err = touch_read_original(touch);
    const esp_err_t first_error = err;
    if (err == ESP_ERR_INVALID_STATE) {
        /* FT3168 can briefly NACK; one bounded retry preserves input without
         * turning a transient bus response into a multi-second UI stall. */
        vTaskDelay(pdMS_TO_TICKS(5));
        err = touch_read_original(touch);
    }
    if (first_error != ESP_OK && !reported) {
        reported = true;
        ESP_LOGW(TAG, "first touch read: %s; retry result %s after %lu ms",
                 esp_err_to_name(first_error), esp_err_to_name(err),
                 (unsigned long)((xTaskGetTickCount() - started) * portTICK_PERIOD_MS));
    }
    return err;
}
static esp_err_t touch_write(esp_lcd_panel_io_handle_t io, int reg, uint8_t value) {
    esp_err_t err = ESP_FAIL;
    for (unsigned attempt = 0; attempt < 4; ++attempt) {
        err = esp_lcd_panel_io_tx_param(io, reg, &value, 1);
        if (err != ESP_ERR_INVALID_STATE) return err;
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    return err;
}
static const sh8601_lcd_init_cmd_t commands[] = {
    {0x11, NULL, 0, 120},
    {0x36, (uint8_t[]){0xF0}, 1, 0},
    {0x3A, (uint8_t[]){0x55}, 1, 0},
    {0x2A, (uint8_t[]){0, 0, 2, 0x17}, 4, 0},
    {0x2B, (uint8_t[]){0, 0, 0, 0xEF}, 4, 0},
    {0x51, (uint8_t[]){0}, 1, 10},
    {0x29, NULL, 0, 10},
};
esp_err_t tg_board_display_new(size_t transfer_size,
    esp_lcd_panel_handle_t *panel, esp_lcd_panel_io_handle_t *io) {
    const i2c_master_bus_config_t i2c = {
        .i2c_port = I2C_NUM_0, .sda_io_num = GPIO_NUM_40,
        .scl_io_num = GPIO_NUM_39, .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c, &bus), TAG, "I2C");
    const spi_bus_config_t spi = {
        .sclk_io_num = GPIO_NUM_47, .data0_io_num = GPIO_NUM_18,
        .data1_io_num = GPIO_NUM_7, .data2_io_num = GPIO_NUM_48,
        .data3_io_num = GPIO_NUM_5, .max_transfer_sz = transfer_size,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &spi, SPI_DMA_CH_AUTO), TAG, "SPI");
    const esp_lcd_panel_io_spi_config_t config = {
        .cs_gpio_num = GPIO_NUM_6, .dc_gpio_num = -1, .spi_mode = 0,
        .pclk_hz = 40 * 1000 * 1000, .trans_queue_depth = 2,
        .lcd_cmd_bits = 32, .lcd_param_bits = 8, .flags.quad_mode = true,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
        &config, &panel_io), TAG, "panel IO");
    const sh8601_vendor_config_t vendor = {
        .init_cmds = commands, .init_cmds_size = sizeof(commands)/sizeof(commands[0]),
        .flags.use_qspi_interface = 1,
    };
    const esp_lcd_panel_dev_config_t dev = {
        .reset_gpio_num = GPIO_NUM_17, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16, .vendor_config = (void *)&vendor,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_sh8601(panel_io, &dev, panel), TAG, "panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(*panel), TAG, "panel reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(*panel), TAG, "panel init");
    *io = panel_io;
    ESP_LOGI(TAG, "RM67162 536x240, transfer %u bytes, BOOT settings", (unsigned)transfer_size);
    return ESP_OK;
}
esp_err_t tg_board_touch_new(esp_lcd_touch_handle_t *touch) {
    esp_lcd_panel_io_i2c_config_t io = ESP_LCD_TOUCH_IO_I2C_FT5x06_CONFIG();
    io.scl_speed_hz = 100000;
    esp_lcd_panel_io_handle_t touch_io = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(bus, &io, &touch_io), TAG, "touch IO");
    const uint8_t normal_mode = 0;
    ESP_RETURN_ON_ERROR(touch_write(touch_io, 0, normal_mode), TAG, "touch normal mode");
    const esp_lcd_touch_config_t config = {
        .x_max = 239, .y_max = 535, .rst_gpio_num = -1, .int_gpio_num = -1,
        .flags = {.swap_xy = 1, .mirror_x = 1, .mirror_y = 0},
    };
    esp_err_t touch_err = ESP_FAIL;
    for (unsigned attempt = 0; attempt < 4; ++attempt) {
        touch_err = esp_lcd_touch_new_i2c_ft5x06(touch_io, &config, touch);
        if (touch_err != ESP_ERR_INVALID_STATE) break;
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    ESP_RETURN_ON_ERROR(touch_err, TAG, "touch init");
    /* FT3168 monitor mode can stop answering polled I2C reads. This USB-powered
     * profile uses continuous polling, so explicitly disable automatic monitor
     * (0x86) and select active power mode (0xA5) after generic driver init.
     * Register reference: LilyGO Arduino_FT3x68.h/cpp, FT3168 example. */
    touch_read_original = (*touch)->read_data;
    (*touch)->read_data = touch_read_diagnosed;
    const uint8_t active = 0;
    ESP_RETURN_ON_ERROR(touch_write(touch_io, 0x86, active), TAG, "touch auto-monitor off");
    ESP_RETURN_ON_ERROR(touch_write(touch_io, 0xA5, active), TAG, "touch active mode");
    return ESP_OK;
}
esp_err_t tg_board_brightness_set(int percent) {
    if (!panel_io) return ESP_ERR_INVALID_STATE;
    if (percent < 0 || percent > 100) return ESP_ERR_INVALID_ARG;
    const uint8_t value = (uint8_t)(percent * 255 / 100);
    return esp_lcd_panel_io_tx_param(panel_io, 0x02005100, &value, 1);
}
esp_err_t tg_board_brightness_init(void) { return tg_board_brightness_set(0); }
#endif
