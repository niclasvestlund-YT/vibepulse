#ifndef TORGET_OTA_UI_H
#define TORGET_OTA_UI_H

#include <stdbool.h>

/*
 * Underhållsoverlayn: fyra stora statiska lägen på LVGL:s topplager, dolda
 * tills KEY3-hållet öppnar fönstret. Overlayn bor UTANFÖR appträdet med
 * flit — den får aldrig ändra en befintlig applayout, bara täcka den.
 *
 * Trådregel: torget_ota_ui_set() tar själv torget_ui_lock() och får därför
 * ALDRIG kallas från LVGL-tasken (adapterlåsets reentrans är overifierad,
 * spec/hardware.md-läxan om spegelvända lås gör oss försiktiga). Tjänsten
 * och underhållstasken är de avsedda anroparna.
 */

typedef enum {
  TG_OTA_UI_HIDDEN,     /* overlayn borta, apparna syns som vanligt */
  TG_OTA_UI_OPEN,       /* "UPDATES ON" + nedräkning av fönstret    */
  TG_OTA_UI_RECEIVING,  /* "RECEIVING" + procent + balk             */
  TG_OTA_UI_VERIFYING,  /* "VERIFYING"                              */
  TG_OTA_UI_RESTARTING, /* "RESTARTING"                             */
  TG_OTA_UI_NOTICE,     /* "UPDATE READY"-takeovern (annonserat bygge) */
} tg_ota_ui_state;

/* Bygg overlayn (dold). Kallas EN gång efter torget_ui_create(), medan
 * UI-låset redan hålls av anroparen — därför låser create inte själv. */
void torget_ota_ui_create(void);

/* Visa läget. percent gäller bara RECEIVING (0-100), seconds_left bara
 * OPEN (nedräkningen). Tar UI-låset; oförändrat innehåll ritas inte om. */
void torget_ota_ui_set(tg_ota_ui_state state, unsigned percent,
                       int seconds_left);

/* Versionsraden under ringen: den KÖRANDE versionen när fönstret öppnas,
 * den INKOMMANDE så fort uppladdningens metadata är läst — svaret på "vad
 * är det som installeras?". Bara enhetens egna fakta, aldrig påhitt.
 * Tar UI-låset med samma 200 ms-regel som set(); ett tappat försök är
 * kosmetik och nästa anrop skriver om. Samma trådregel som set(). */
void torget_ota_ui_set_version(const char *version);

/* Notisens avfärdande: overlayn slukar touch, och ett tryck i NOTICE-läget
 * sätter en atomär flagga som tjänstens vakt konsumerar (LVGL-tasken får
 * aldrig själv röra tjänstelogik). Returnerar true en gång per tryck. */
bool torget_ota_ui_take_tap(void);

/* JA-pillrets tryck: det ENDA trycket som betyder "uppdatera nu" — allt
 * annat glas snoozar. Konsumeras av vakten precis som take_tap. */
bool torget_ota_ui_take_update_tap(void);

#endif
