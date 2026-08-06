# Kontraktskomponenten: headrarna (torget_app.h + torget.h ur platform/)
# OCH designsystemets fonter. Fonterna bor här — inte i main — för att
# apparna refererar dem (extern const lv_font_t) och länkar via sitt
# REQUIRES på den här komponenten; i main-biblioteket nådde länkordningen
# aldrig fram (upptäckt i första targetbygget).
file(GLOB TG_FONTS "${CMAKE_CURRENT_LIST_DIR}/../../platform/fonts/*.c")

idf_component_register(
  SRCS ${TG_FONTS}
  INCLUDE_DIRS "../../platform")

# lv_font_conv-fonterna inkluderar "lvgl/lvgl.h" om inte det här är satt;
# ESP-komponenten lägger bara lvgl-roten på sökvägen ("lvgl.h").
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
