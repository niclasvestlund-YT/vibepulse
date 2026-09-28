/* SPDX-License-Identifier: Apache-2.0 */
/* ESP32-S3-Touch-AMOLED-1.75 only (not 1.75C).
 * Panel sequence, gap and touch transform from Waveshare's maintained BSP:
 * ESP32-S3-Touch-AMOLED-1.75 @ e4344e70, firmware/brookesia/components/
 * waveshare__esp32_s3_touch_amoled_1_75/esp32_s3_touch_amoled_1_75.c.
 * Pins cross-checked against that revision's schematic and hardware reference.
 * LCD/touch use VCC3V3; the vendor display path does not reprogram the PMIC.
 * Keep other peripherals and the original board's IMU calibration disabled.
 */
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_lcd_co5300.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_touch_cst9217.h"

static const char *TAG = "board-175";
static i2c_master_bus_handle_t round_bus;
static esp_lcd_panel_io_handle_t round_io;

static const co5300_lcd_init_cmd_t round_init[] = {
    {0xFE, (uint8_t[]){0x20}, 1, 0},
    {0x19, (uint8_t[]){0x10}, 1, 0},
    {0x1C, (uint8_t[]){0xA0}, 1, 0},
    {0xFE, (uint8_t[]){0x00}, 1, 0},
    {0xC4, (uint8_t[]){0x80}, 1, 0},
    {0x3A, (uint8_t[]){0x55}, 1, 0},
    {0x35, (uint8_t[]){0x00}, 1, 0},
    {0x53, (uint8_t[]){0x20}, 1, 0},
    /* Start dark; caller sets a measured, low diagnostic brightness. */
    {0x51, (uint8_t[]){0x00}, 1, 0},
    {0x63, (uint8_t[]){0xFF}, 1, 0},
    {0x2A, (uint8_t[]){0x00, 0x06, 0x01, 0xD7}, 4, 0},
    {0x2B, (uint8_t[]){0x00, 0x00, 0x01, 0xD1}, 4, 600},
    {0x11, NULL, 0, 600},
    {0x29, NULL, 0, 0},
};

esp_err_t tg_board_display_new(size_t transfer_size,
    esp_lcd_panel_handle_t *panel, esp_lcd_panel_io_handle_t *io) {
    const spi_bus_config_t bus_cfg = CO5300_PANEL_BUS_QSPI_CONFIG(
        GPIO_NUM_38, GPIO_NUM_4, GPIO_NUM_5, GPIO_NUM_6, GPIO_NUM_7, transfer_size);
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO), TAG, "SPI");
    esp_lcd_panel_io_spi_config_t io_cfg = CO5300_PANEL_IO_QSPI_CONFIG(GPIO_NUM_12, NULL, NULL);
    io_cfg.trans_queue_depth = 2;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
        &io_cfg, &round_io), TAG, "panel IO");
    const co5300_vendor_config_t vendor = {
        .init_cmds = round_init,
        .init_cmds_size = sizeof(round_init) / sizeof(round_init[0]),
        .flags.use_qspi_interface = 1,
    };
    const esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = GPIO_NUM_39, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16, .vendor_config = (void *)&vendor,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_co5300(round_io, &panel_cfg, panel), TAG, "panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(*panel, 6, 0), TAG, "gap");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(*panel), TAG, "reset GPIO39");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(*panel), TAG, "panel init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(*panel, true), TAG, "display on");
    *io = round_io;
    ESP_LOGI(TAG, "1.75 466x466 CO5300, native orientation, gap 6/0, transfer=%u",
             (unsigned)transfer_size);
    return ESP_OK;
}

esp_err_t tg_board_touch_new(esp_lcd_touch_handle_t *touch) {
    const i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0, .sda_io_num = GPIO_NUM_15, .scl_io_num = GPIO_NUM_14,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &round_bus), TAG, "touch I2C");
    esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_CST9217_CONFIG();
    io_cfg.scl_speed_hz = 400000;
    esp_lcd_panel_io_handle_t touch_io = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(round_bus, &io_cfg, &touch_io), TAG, "touch IO");
    const esp_lcd_touch_config_t config = {
        .x_max = 466, .y_max = 466,
        .rst_gpio_num = GPIO_NUM_40, .int_gpio_num = GPIO_NUM_11,
        .levels = {.reset = 0, .interrupt = 0},
        .flags = {.swap_xy = 0, .mirror_x = 1, .mirror_y = 1},
    };
    return esp_lcd_touch_new_i2c_cst9217(touch_io, &config, touch);
}

esp_err_t tg_board_brightness_set(int percent) {
    if (!round_io) return ESP_ERR_INVALID_STATE;
    if (percent < 0 || percent > 100) return ESP_ERR_INVALID_ARG;
    uint8_t value = (uint8_t)(percent * 255 / 100);
    return esp_lcd_panel_io_tx_param(round_io, 0x02005100, &value, 1);
}
esp_err_t tg_board_brightness_init(void) { return tg_board_brightness_set(0); }
