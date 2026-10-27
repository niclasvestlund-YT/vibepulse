if(NOT IDF_TARGET STREQUAL "esp32p4")
    # The standalone 7B project owns this UI and its fonts. ESP-IDF discovers
    # every root component, so keep existing S3 targets free of P4 app inputs.
    idf_component_register()
    return()
endif()

idf_component_register(SRCS "grok_ui.c" "assets/grok_avatar.c"
    "fonts/grok_inter_18.c" "fonts/grok_inter_24.c" "fonts/grok_inter_28.c"
    "fonts/grok_inter_46.c" INCLUDE_DIRS "." REQUIRES lvgl__lvgl)
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
