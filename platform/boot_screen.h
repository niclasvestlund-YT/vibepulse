#ifndef TORGET_BOOT_SCREEN_H
#define TORGET_BOOT_SCREEN_H

/*
 * Bootskärmen: i stället för att apparna visar streck och NO DATA under
 * uppstartens första halvminut berättar plattformen ärligt var i kedjan
 * den är — WIFI → TIME → DATA — och kliver undan i samma ögonblick som
 * första riktiga datan landat (beslut 2026-08-14, tredje efterfrågan).
 *
 * Inga påhittade förlopp: varje steg tänds av sin verkliga signal
 * (IP-eventet, SNTP-synken, första lyckade hämtningen), och en yttre
 * timeout i värdlagret tar ner skärmen om nätet aldrig kommer — bakom
 * den står apparnas ärliga NO DATA-läge.
 *
 * Lagerordning: skapas på lv_layer_top FÖRE OTA-overlayn, så en
 * OTA-omstarts READY-ring alltid vinner över bootskärmen.
 *
 * Trådregel: stage() tar UI-låset självt med samma 200 ms-tålamod som
 * torget_ota_ui_set — anropas från event-/nättaskar, aldrig LVGL-tasken.
 */

typedef enum {
  TG_BOOT_WIFI_UP,   /* IP:t är hemma — WIFI tänds                  */
  TG_BOOT_TIME_OK,   /* SNTP satte klockan — TIME tänds             */
  TG_BOOT_DATA_OK,   /* första hämtningen landade — skärmen kliver av */
  TG_BOOT_GIVE_UP,   /* värdlagrets timeout — kliv av ändå          */
} tg_boot_stage;

/* Bygg och VISA skärmen (dold igen via DATA_OK/GIVE_UP). Kallas under
 * anroparens UI-lås, före torget_ota_ui_create. */
void torget_boot_screen_create(void);

void torget_boot_screen_stage(tg_boot_stage stage);

#endif
