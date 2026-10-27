idf_component_register(SRCS "main.c" "voice_device.c" INCLUDE_DIRS "."
    REQUIRES app_grok esp_timer esp_system esp_driver_usb_serial_jtag json
    esp_http_client esp_event esp_netif esp_wifi nvs_flash esp_driver_i2s mbedtls)
