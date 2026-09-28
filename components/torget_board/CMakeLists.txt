set(board_requires torget_app esp_lcd esp_driver_gpio esp_driver_i2c esp_driver_spi)
if("$ENV{TORGET_BOARD}" STREQUAL "waveshare_18_v2")
    list(APPEND board_requires waveshare__esp32_s3_touch_amoled_1_8)
else()
    list(APPEND board_requires waveshare__esp32_s3_touch_amoled_2_16
        espressif__esp_lcd_co5300 waveshare__esp_lcd_touch_cst9217)
endif()
idf_component_register(SRCS "board.c" "board_191.c" INCLUDE_DIRS "." REQUIRES ${board_requires})
