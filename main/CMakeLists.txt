# Värdlagret för kortet + det delade plattforms-UI:t. Apparna är egna
# komponenter (components/app_*) och dras in via REQUIRES — registry.c
# refererar deras torget_app_t. Fonterna bor i torget_app-komponenten
# (designsystemet är plattformens, och apparna länkar dem den vägen).
# Companion-apparna (Solelkollen, Vibbe/Buddy) är frivilliga: finns de
# utcheckade dras komponenten in och TORGET_HAVE_* grindar registerposten i
# registry.c. En färsk klon utifrån bygger bara VibePulse.
# torget_ota: bootens hälsogrind (boot_health.h) och KEY3:s underhållshåll.
set(TORGET_MAIN_REQUIRES torget_app app_tokens torget_ota)
# OBS $ENV{}: den här filen körs TVÅ gånger — en gång i ESP-IDF:s
# early-expansion (då REQUIRES läses ut) och en gång i det riktiga bygget. I
# expansionspasset syns INTE cache-/normalvariablerna som rot-CMakeLists.txt
# satte (TORGET_*_DIR är tomma där), bara miljövariabler överlever dit. Roten
# speglar därför de upplösta sökvägarna till miljön, och vi grindar på $ENV{}
# så att REQUIRES och TORGET_HAVE_*-definitionen nedan ALLTID ser samma värde.
# Grindar vi på ${TORGET_*_DIR} får vi makrot men inte include-katalogen, och
# registry.c faller på "app_solelkollen.h: No such file".
if(EXISTS "$ENV{TORGET_SOLELKOLLEN_DIR}/app_solelkollen")
  list(APPEND TORGET_MAIN_REQUIRES app_solelkollen)
endif()
if(EXISTS "$ENV{TORGET_BUDDY_DIR}/app_buddy")
  list(APPEND TORGET_MAIN_REQUIRES app_buddy)
endif()

idf_component_register(
  SRCS "main.c"
       "rotation.c"
       "registry.c"
       "../platform/torget_ui.c"
       "../platform/boot_screen.c"
  # ".." ger `#include "secrets.h"` (gitignorad, kopieras ur secrets.h.example)
  INCLUDE_DIRS "." ".." "../platform"
  REQUIRES ${TORGET_MAIN_REQUIRES}
           nvs_flash
           esp_wifi
           esp_netif
           esp_event
           esp_timer)

if(EXISTS "$ENV{TORGET_SOLELKOLLEN_DIR}/app_solelkollen")
  target_compile_definitions(${COMPONENT_LIB} PRIVATE TORGET_HAVE_SOLELKOLLEN)
endif()
if(EXISTS "$ENV{TORGET_BUDDY_DIR}/app_buddy")
  target_compile_definitions(${COMPONENT_LIB} PRIVATE TORGET_HAVE_BUDDY)
endif()

# lv_font_conv-fonterna inkluderar "lvgl/lvgl.h" om inte det här är satt.
# ESP-komponenten lägger bara lvgl-roten på sökvägen, alltså "lvgl.h" — samma
# fontfiler bygger i simulatorn utan flaggan eftersom FetchContent råkar ge
# föräldrakatalogen. Definitionen är den officiella vägen ut ur den skillnaden.
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
