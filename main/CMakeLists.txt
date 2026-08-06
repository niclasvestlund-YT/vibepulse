# Värdlagret för kortet + det delade plattforms-UI:t. Apparna är egna
# komponenter (components/app_*) och dras in via REQUIRES — registry.c
# refererar deras torget_app_t. Fonterna bor i torget_app-komponenten
# (designsystemet är plattformens, och apparna länkar dem den vägen).
idf_component_register(
  SRCS "main.c"
       "rotation.c"
       "registry.c"
       "../platform/torget_ui.c"
  # ".." ger `#include "secrets.h"` (gitignorad, kopieras ur secrets.h.example)
  INCLUDE_DIRS "." ".." "../platform"
  REQUIRES torget_app
           app_solelkollen
           app_tokens
           nvs_flash
           esp_wifi
           esp_netif
           esp_event
           esp_timer)

# lv_font_conv-fonterna inkluderar "lvgl/lvgl.h" om inte det här är satt.
# ESP-komponenten lägger bara lvgl-roten på sökvägen, alltså "lvgl.h" — samma
# fontfiler bygger i simulatorn utan flaggan eftersom FetchContent råkar ge
# föräldrakatalogen. Definitionen är den officiella vägen ut ur den skillnaden.
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
