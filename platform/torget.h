#ifndef TORGET_H
#define TORGET_H

#include <stdbool.h>
#include <stdint.h>

#include "lvgl.h"

#include "torget_app.h"

/*
 * Plattformens API mot apparna. Två världar implementerar det: main/main.c
 * (ESP-targetet: riktigt lås, riktig klocka, riktigt WiFi) och sim/main.c
 * (Macen: no-op-lås, SDL-klocka, fixtures i stället för nät). Appkod som
 * håller sig till det här hörnet + LVGL bygger oförändrad i båda världarna —
 * samma regel som höll bänken och hyllan ihop i Solelkollen.
 */

/* ---- värdlagret (olika per värld, deklarerade här) ---------------------- */

/* LVGL-låset. Targetet pratar med esp_lv_adapter_lock(-1) DIREKT — aldrig
 * bsp_display_lock(), vars boolretur är spegelvänd (se spec/hardware.md).
 * Simulatorn är entrådad: no-op. Allt som rör LVGL-objekt eller delas med
 * en apptask sker under det här låset. */
void torget_ui_lock(void);
void torget_ui_unlock(void);

/* Monoton mikrosekundsklocka — tickerns och stale-trösklarnas tidsbas.
 * Serverklockor används aldrig som tidsbas (Solelkollen-regeln). */
int64_t torget_now_us(void);

/* Blockera tills nätet är uppe och klockan SNTP-satt (TLS kräver rimlig
 * tid). Appens hämttask kallar detta först av allt. I simulatorn: no-op —
 * fixtures behöver inget nät. */
void torget_net_wait(void);

/* Appens sätt att hålla skärmen vaken: "något händer hos mig". Solelkollen
 * kallar den när solen producerar, Tokenmätaren när tokens brinner. Utan
 * anrop på 15 min (och utan touch på 30 s) rampar plattformen ner till
 * nattljus — generaliseringen av "solen är villkoret, inte klockan".
 * Kallas under torget_ui_lock(). */
void torget_keep_awake(void);

/* ---- plattforms-UI:t (delat, platform/torget_ui.c) ---------------------- */

/* Bygg drift-lagret, alla appars rötter (via create i registret) och
 * launchern; gå in i app 0. Kallas en gång efter lv_init + display. */
void torget_ui_create(void);

/* Öppna launchern. Apparna äger gesten (långtryck i sin UI) och kallar hit —
 * plattformen kan inte sno åt sig gesten utan att sabba apparnas svep. */
void torget_launcher_open(void);

/* Gå in i app idx (registerordning). Launcherns ikontryck går den här vägen;
 * bänken använder den för att BMP-dumpa alla appar obevakat. */
void torget_app_show(int idx);

/* Nästa app i registret (från launchern: app 0). KEY3-knappens väg på
 * targetet, tangent N i bänken — appväxling utan att röra glaset. */
void torget_app_next(void);

/* Pixeldriften mot inbränning: allt UI bor i en låda som vandrar ett par
 * pixlar per minut. Apparna behöver aldrig bry sig; exponerad för värdlager
 * som vill styra takten i test. */
void torget_drift_step(void);

#endif
