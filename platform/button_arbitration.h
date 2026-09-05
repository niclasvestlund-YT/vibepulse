#ifndef TORGET_BUTTON_ARBITRATION_H
#define TORGET_BUTTON_ARBITRATION_H

#include <stdbool.h>

#include "button_policy.h"

/*
 * KEY3:s SKILJEDOM — vem av glasets ägare som får knappen den här ticken.
 *
 * `tg_button_update` (components/torget_ota/button_policy.c) avgör VAD fingret
 * gjorde: kort tryck, mellanhåll-och-släpp, eller ett fullbordat tresekunders
 * håll. Den här filen avgör VAD DET BETYDER just nu, givet vem som äger
 * glaset: setupfönstret, underhållsfönstret, UPDATE READY-notisen, SETTINGS
 * — eller ingen.
 *
 * Den bodde i `tick_cb` i main/main.c: 110 rader beslut i värdlagret, som
 * `sim/main.c` inte kunde nå. Simulatorn kallade `torget_settings_open()`
 * direkt i sin statiska QA och var därför INTE spec för gesten, nödutgången,
 * avsiktsöverlämningen eller den asynkrona notisstängningen — tvärtemot
 * AGENTS.md ("Värdlagren är tunna … UI-beteende hör hemma i appen/platform/,
 * aldrig i main/main.c eller sim/main.c" och "Simulatorn är specen").
 * Codex P1 på PR #72.
 *
 * REN med flit: inga tjänsteanrop, inga lås, ingen tid, inget globalt
 * tillstånd. In går ett observerbart läge, ut går vad värden ska göra. Båda
 * värdarna matar samma funktion och verkställer samma svar, och hela tabellen
 * — inklusive lägen som bara kan uppstå asynkront från andra tasks — låses i
 * värdtester (test/test_key3_arbitration.c) i stället för att läsas ur källan.
 *
 * VARFÖR EN STRUKT UT OCH INTE EN ENUM: två utdata kan gälla samma tick.
 * Menyn stängs FÖRE knappkedjan när ett fönster tar över glaset, och samma
 * tick kan knappen dessutom betyda något för fönstret. En ensam enum hade
 * tvingat fram en prioritering som originalet inte har — och tyst tappat den
 * andra halvan.
 */

typedef struct {
  /* Vad knappolicyn såg. */
  tg_button_action key;
  /* Setupfönstret äger knappen — INKLUSIVE STARTING, medan AP:n kommer upp. */
  bool setup_owns_input;
  /* Underhållsfönstret (OTA) står öppet. */
  bool maintenance_open;
  /* UPDATE READY-takeovern syns. Ett UI-läge, inte ett öppet fönster: den
   * annonseras av maintenance_ui_task utan någon knapphändelse att hänga
   * beslutet på, och det är precis det fallet som bara en ren funktion kan
   * testas på. */
  bool notice_visible;
  /* SETTINGS-menyn står uppe. */
  bool menu_open;
} tg_button_inputs;

/*
 * Vad värden ska göra. Varje fält motsvarar exakt ETT anrop hos värden, så
 * tabelltestet och verkställandet inte kan glida isär. Flera fält kan vara
 * sanna samtidigt; alla falska betyder "gör ingenting".
 *
 * Här finns MED FLIT inget "öppna underhållsfönstret". Fönstret öppnas bara
 * av ett tryck på UPDATE-raden inne i menyn (settings-avsikten), aldrig av
 * skiljedomen och aldrig av ett skript. Samtyckesmodellen är därmed inte
 * längre en gren att läsa rätt utan en utdata som inte existerar.
 */
typedef struct {
  /* torget_settings_close() */
  bool close_menu;
  /* Menyn äger glaset den här ticken: uppdatera adressen och lyft den
   * överst (torget_settings_set_address() + torget_settings_keep_foreground()).
   * Måste ske VARJE tick — NO NETWORK-sidan ritar om sin nedräkning varje
   * sekund och lyfter sig då, så en engångslyftning begravs inom en sekund. */
  bool menu_foreground;
  /* torget_wifi_setup_request_close() */
  bool close_setup;
  /* torget_ota_service_close_maintenance() */
  bool close_maintenance;
  /* torget_wifi_setup_request_open() — en BEGÄRAN. Setupvakten äger
   * överlämningen av port 80; att stänga OTA-fönstret här hade gjort
   * portbytet till en kapplöpning mellan två vakter som pollar var 500:e ms. */
  bool request_setup_open;
  /* torget_settings_open(version, ip) */
  bool open_menu;
  /* torget_app_next() */
  bool next_app;
  /* tk_needs_you_send_panic() */
  bool panic;
} tg_button_outputs;

/* Ren skiljedom. `out` skrivs alltid helt, oavsett `in`. */
void tg_button_arbitrate(const tg_button_inputs *in, tg_button_outputs *out);

#endif
