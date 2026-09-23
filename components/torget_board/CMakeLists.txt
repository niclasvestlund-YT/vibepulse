idf_component_register(SRCS "board.c" INCLUDE_DIRS "."
    REQUIRES torget_app esp_lcd esp_driver_gpio esp_driver_i2c esp_driver_spi
             waveshare__esp32_s3_touch_amoled_2_16
             espressif__esp_lcd_co5300 waveshare__esp_lcd_touch_cst9217)
