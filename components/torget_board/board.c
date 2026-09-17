#include "torget_board.h"

#ifndef TORGET_BOARD_241_V2
#include "bsp/touch.h"

esp_err_t tg_board_display_new(size_t transfer_size,
    esp_lcd_panel_handle_t *panel, esp_lcd_panel_io_handle_t *io) {
    const bsp_display_config_t config = {.max_transfer_sz = transfer_size};
    return bsp_display_new(&config, panel, io);
}
esp_err_t tg_board_touch_new(esp_lcd_touch_handle_t *touch) {
    bsp_display_cfg_t config = {
        .touch_flags = {.swap_xy = 1, .mirror_x = 0, .mirror_y = 1},
    };
    return bsp_touch_new(&config, touch);
}
esp_err_t tg_board_brightness_init(void) { return bsp_display_brightness_init(); }
esp_err_t tg_board_brightness_set(int percent) { return bsp_display_brightness_set(percent); }

#else
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_io_expander_tca9554.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_sh8601.h"
#include "esp_lcd_touch_ft5x06.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* Waveshare 2.41 V2 ONLY. Pin/reset mapping and RM690B0 init are taken
 * from waveshareteam/ESP32-S3-Touch-AMOLED-2.41-V2 @ 0eaf16c.
 * The vendor uses the SH8601 transport driver for this RM690B0 panel.
 * V1 has different reset wiring and must not use this board selection. */
static const char *TAG = "board-241-v2";
static i2c_master_bus_handle_t bus;
static esp_io_expander_handle_t expander;
static esp_lcd_panel_io_handle_t panel_io;

static const sh8601_lcd_init_cmd_t init_commands[] = {
    {0xFE, (uint8_t[]){0x20}, 1, 0},
    {0x26, (uint8_t[]){0x0A}, 1, 0},
    {0x24, (uint8_t[]){0x80}, 1, 0},
    {0xFE, (uint8_t[]){0x00}, 1, 0},
    {0x3A, (uint8_t[]){0x55}, 1, 0},
    {0xC2, (uint8_t[]){0x00}, 1, 10},
    {0x35, NULL, 0, 0},
    {0x51, (uint8_t[]){0x00}, 1, 10},
    {0x11, NULL, 0, 80},
    {0x2A, (uint8_t[]){0x00, 0x10, 0x00, 0xD1}, 4, 0},
    {0x2B, (uint8_t[]){0x00, 0x00, 0x00, 0x57}, 4, 0},
    {0x29, NULL, 0, 10},
    /* Vendor's landscape pair: MADCTL 0x30 + touch swap_xy/mirror_y. */
    {0x36, (uint8_t[]){0x30}, 1, 0},
    {0x51, (uint8_t[]){0x00}, 1, 0},
};

static esp_err_t reset_pin(uint32_t pin) {
    ESP_RETURN_ON_ERROR(esp_io_expander_set_level(expander, pin, 1), TAG, "reset high");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(esp_io_expander_set_level(expander, pin, 0), TAG, "reset low");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(esp_io_expander_set_level(expander, pin, 1), TAG, "reset release");
    vTaskDelay(pdMS_TO_TICKS(200));
    return ESP_OK;
}

esp_err_t tg_board_display_new(size_t transfer_size,
    esp_lcd_panel_handle_t *panel, esp_lcd_panel_io_handle_t *io) {
    const i2c_master_bus_config_t i2c_config = {
        .i2c_port = I2C_NUM_0, .sda_io_num = GPIO_NUM_47,
        .scl_io_num = GPIO_NUM_48, .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c_config, &bus), TAG, "I2C");
    ESP_RETURN_ON_ERROR(esp_io_expander_new_i2c_tca9554(bus,
        ESP_IO_EXPANDER_I2C_TCA9554_ADDRESS_000, &expander), TAG, "expander");
    ESP_RETURN_ON_ERROR(esp_io_expander_set_dir(expander,
        IO_EXPANDER_PIN_NUM_0 | IO_EXPANDER_PIN_NUM_1, IO_EXPANDER_OUTPUT), TAG, "reset outputs");
    const spi_bus_config_t spi_config = {
        .sclk_io_num = GPIO_NUM_10, .data0_io_num = GPIO_NUM_11,
        .data1_io_num = GPIO_NUM_12, .data2_io_num = GPIO_NUM_13,
        .data3_io_num = GPIO_NUM_14, .max_transfer_sz = transfer_size,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &spi_config, SPI_DMA_CH_AUTO), TAG, "SPI");
    const esp_lcd_panel_io_spi_config_t io_config = {
        .cs_gpio_num = GPIO_NUM_9, .dc_gpio_num = -1, .spi_mode = 0,
        .pclk_hz = 40 * 1000 * 1000, .trans_queue_depth = 2,
        .lcd_cmd_bits = 32, .lcd_param_bits = 8, .flags.quad_mode = true,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
        &io_config, &panel_io), TAG, "panel IO");
    const sh8601_vendor_config_t vendor = {
        .init_cmds = init_commands,
        .init_cmds_size = sizeof(init_commands) / sizeof(init_commands[0]),
        .flags.use_qspi_interface = 1,
    };
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = -1, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16, .vendor_config = (void *)&vendor,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_sh8601(panel_io, &panel_config, panel), TAG, "panel");
    ESP_RETURN_ON_ERROR(reset_pin(IO_EXPANDER_PIN_NUM_0), TAG, "panel reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(*panel), TAG, "panel init");
    /* Native portrait has x gap 16; after 90 degrees the gap is in y.
       Both the vendor factory and Arduino flush callbacks use this pair. */
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(*panel, 0, 16), TAG, "panel gap");
    *io = panel_io;
    ESP_LOGI(TAG, "Waveshare 2.41 V2: 600x450 landscape, BOOT opens SETTINGS");
    return ESP_OK;
}

esp_err_t tg_board_touch_new(esp_lcd_touch_handle_t *touch) {
    ESP_RETURN_ON_ERROR(reset_pin(IO_EXPANDER_PIN_NUM_1), TAG, "touch reset");
    esp_lcd_panel_io_i2c_config_t io_config = ESP_LCD_TOUCH_IO_I2C_FT5x06_CONFIG();
    io_config.scl_speed_hz = 400 * 1000;
    esp_lcd_panel_io_handle_t touch_io = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(bus, &io_config, &touch_io), TAG, "touch IO");
    const esp_lcd_touch_config_t config = {
        .x_max = 449, .y_max = 599, .rst_gpio_num = -1, .int_gpio_num = GPIO_NUM_3,
        .levels = {.reset = 0, .interrupt = 0},
        .flags = {.swap_xy = 1, .mirror_x = 0, .mirror_y = 1},
    };
    return esp_lcd_touch_new_i2c_ft5x06(touch_io, &config, touch);
}

esp_err_t tg_board_brightness_set(int percent) {
    if (!panel_io) return ESP_ERR_INVALID_STATE;
    if (percent < 0 || percent > 100) return ESP_ERR_INVALID_ARG;
    const uint8_t value = (uint8_t)(percent * 255 / 100);
    return esp_lcd_panel_io_tx_param(panel_io, 0x02005100, &value, 1);
}
esp_err_t tg_board_brightness_init(void) { return tg_board_brightness_set(0); }
#endif
