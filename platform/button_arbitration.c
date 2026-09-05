#include "button_arbitration.h"

void tg_button_arbitrate(const tg_button_inputs *in, tg_button_outputs *out) {
  const tg_button_outputs none = {0};
  *out = none;

  /* Menyn är en vägvisare, aldrig en konkurrent: så fort ETT RIKTIGT fönster
   * äger glaset stänger den sig. Det är det som ger fönstren företräde — inte
   * lagerordningen, som inte betyder något här (både setupfönstret och
   * OTA-overlayn lyfter sig själva i sina egna set()).
   *
   * Två vägar in som ingen knappgren kan fånga, båda utlösta från ANDRA tasks
   * medan menyn redan står uppe:
   *   - notisen annonseras av maintenance_ui_task. Utan detta lade den sig
   *     över en meny som fortfarande hade open=true, och LATER avslöjade den
   *     bortglömda menyn.
   *   - setupfönstret öppnar sig SJÄLVT efter 90 s utan adress. Då blev
   *     owns_input sant, kedjan nedan åt knapphändelsen och stängde ett
   *     fönster som inte syntes, medan menyn låg kvar överst och lovade
   *     "KEY3 CLOSES" i foten. Det synliga svarade inte, det osynliga dog. */
  const bool window_owns_glass =
      in->notice_visible || in->setup_owns_input || in->maintenance_open;

  /* FÖRE knappkedjan, och det är hela poängen: stängningen ska hinna ske på
   * den tick då fönstret tar över, så att nästa tryck landar på det man
   * faktiskt ser. Kedjan nedan läser därför den STÄNGDA menyn, inte den som
   * stod uppe när ticken började. */
  bool menu_open = in->menu_open;
  if (menu_open) {
    if (window_owns_glass) {
      out->close_menu = true;
      menu_open = false;
    } else {
      out->menu_foreground = true;
    }
  }

  if (in->setup_owns_input) {
    /* Även STARTING äger knappen. AP/skanning kan ta sekunder och under den
     * tiden får det utlösande släppet aldrig bli appväxling eller panik.
     * request_close() ignorerar STARTING men stänger OPEN/JOINING/JOINED/
     * FAILED, så nödutgången finns kvar när fönstret väl kan stängas. */
    if (in->key == TG_BUTTON_NEXT_APP || in->key == TG_BUTTON_PANIC)
      out->close_setup = true;
  } else if (in->maintenance_open) {
    /* Medan underhållsfönstret äger glaset stänger VARJE släpp det — ett kort
     * tryck som panikhållet — så nödutgången aldrig hänger på att hinna
     * utanför panikfönstret (en riktig fälla, hittad på hårdvara 2026-08-16:
     * ett ~2 s tryck panikade i stället för att stänga och fönstret satt
     * kvar). Tio minuter utan nödutgång fick en frisk enhet att se hängd ut.
     * Ingen panik avfyras medan fönstret är uppe.
     *
     * Ett NYTT fullt 3 s-håll byter fönster: setupfönstret BEGÄRS öppnat.
     * Det är vägen till WIFI SETUP på en panel som HAR nät (håll–håll) —
     * utan den kunde ett nytt nät bara läras ut när panelen redan var
     * strandad. Nödutgången är intakt: varje släpp FÖRE tre sekunder
     * stänger, bara ett medvetet fullbordat håll byter. */
    if (in->key == TG_BUTTON_NEXT_APP || in->key == TG_BUTTON_PANIC)
      out->close_maintenance = true;
    else if (in->key == TG_BUTTON_OPEN_MAINTENANCE)
      out->request_setup_open = true;
  } else if (menu_open) {
    /* Menyn ärver fönstrens nödutgång: VARJE knapphändelse stänger den, kort
     * tryck som mellanhåll. Ett fullbordat håll stänger också — menyn öppnar
     * aldrig sig själv igen ovanpå sig själv. Ingen panik avfyras medan menyn
     * är uppe, exakt som med de två fönstren. */
    if (in->key != TG_BUTTON_NONE) out->close_menu = true;
  } else if (in->key == TG_BUTTON_NEXT_APP) {
    out->next_app = true;
  } else if (in->key == TG_BUTTON_PANIC) {
    /* Mellanhåll-och-släpp med stängt fönster: neka allt Needs You parkerat. */
    out->panic = true;
  } else if (in->key == TG_BUTTON_OPEN_MAINTENANCE && !in->notice_visible) {
    /* UPDATE READY-takeovern äger glaset utan att vara ett öppet
     * underhållsfönster, så maintenance_open ovan säger nej och hållet nådde
     * ända hit. Menyn öppnades då BAKOM notisen: open=true, inget syntes, och
     * den dök upp senare när notisen gick bort. Takeovern har sina egna svar
     * (UPDATE-pillret och LATER, med fingret) — hållet gör ingenting alls
     * medan den syns. Villkoret sitter på just den HÄR grenen och inte som ett
     * eget block: ett kort tryck och paniken ska bete sig precis som förut. */
    out->open_menu = true;
  }
}
