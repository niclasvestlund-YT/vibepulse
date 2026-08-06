#ifndef SOLGLANCE_ROTATION_H
#define SOLGLANCE_ROTATION_H

#include "lvgl.h"

/*
 * P24: auto-rotation via QMI8658-IMU:n. Panelen är kvadratisk, så layouten
 * är identisk i alla fyra riktningar — det som roteras är MADCTL (hårdvara,
 * ingen renderingskostnad) och touchkoordinaterna (indev-wrapper), ALLTID
 * i samma grepp: spec/hardware.md förbjuder att ändra bara ena sidan.
 *
 * Orienteringen ANKRAS vid första stabila stående avläsningen: som skärmen
 * står då definieras som rättvänt. Därefter följer bilden med när man
 * vrider enheten (kvartsvarv, 1,6 s hysteres, död zon mot diagonaler).
 * Antagandet är att den står rätt när den får ström — bootar man den
 * medvetet upp och ner är det upp och ner som blir "rätt" den sessionen.
 *
 * Startas EFTER bsp_display_start(); tar över touch-indevens read-callback.
 */
void sg_rotation_start(lv_indev_t *touch);

#endif
