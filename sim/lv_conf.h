/* LVGL-konfig för SDL-simulatorn. Målets konfig ägs av esp_lvgl_adapter och
 * sätts i P11 — den här filen gäller bara Mac-bygget. */
#ifndef LV_CONF_H
#define LV_CONF_H

#define LV_COLOR_DEPTH 16
#define LV_USE_SDL 1
/* Brews SDL2-CMake-target pekar include-sökvägen IN i SDL2-katalogen, så
 * LVGL:s default <SDL2/SDL.h> missar. <SDL.h> är formen som matchar. */
#define LV_SDL_INCLUDE_PATH <SDL.h>
#define LV_USE_SPAN 1
#define LV_USE_TILEVIEW 1
#define LV_USE_FLEX 1
#define LV_USE_LABEL 1
#define LV_FONT_DEFAULT &lv_font_montserrat_14
#define LV_USE_FONT_COMPRESSED 0
#define LV_DEF_REFR_PERIOD 15
#define LV_USE_STDLIB_MALLOC LV_STDLIB_CLIB
#define LV_USE_STDLIB_STRING LV_STDLIB_CLIB
#define LV_USE_STDLIB_SPRINTF LV_STDLIB_CLIB
#define LV_USE_OS LV_OS_NONE
/* Framebuffer-dumpar till BMP: P18-verktyget. Pixelexakt oavsett fönster. */
#define LV_USE_SNAPSHOT 1

#endif
