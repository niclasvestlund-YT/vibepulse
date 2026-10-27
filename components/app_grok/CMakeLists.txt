idf_component_register(SRCS "grok_ui.c" "assets/grok_avatar.c"
    "fonts/grok_inter_18.c" "fonts/grok_inter_24.c" "fonts/grok_inter_28.c"
    "fonts/grok_inter_46.c" INCLUDE_DIRS "." REQUIRES lvgl__lvgl)
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
