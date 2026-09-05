/*
 * KEY3:s skiljedom som en TABELL, inte som en läsning av källan.
 *
 * Åtta invarianter, var och en med en riktig incident bakom sig och var och en
 * pinnad för sig så att en mutation slår i exakt ett test. Sju av dem gick
 * tidigare bara att kontrollera genom att läsa `tick_cb` i main/main.c — och
 * en av dem, den asynkrona notisstängningen, gick inte att testa alls: den
 * utlöses av maintenance_ui_task utan någon knapphändelse att hänga ett test
 * på. Det är hela skälet till att skiljedomen är en ren funktion.
 */

#include <stdio.h>
#include <string.h>

#include "../platform/button_arbitration.h"
#include "../components/torget_wifi/wifi_slots.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tg_button_outputs arb(tg_button_action key, bool setup_owns_input,
                             bool maintenance_open, bool notice_visible,
                             bool menu_open) {
  const tg_button_inputs in = {
      .key = key,
      .setup_owns_input = setup_owns_input,
      .maintenance_open = maintenance_open,
      .notice_visible = notice_visible,
      .menu_open = menu_open,
  };
  /* Skräpfyllt före anropet: en utdata som inte skrivs ska synas som ett fel,
   * inte ärvas från anroparens stack. */
  tg_button_outputs out;
  memset(&out, 0xAA, sizeof out);
  tg_button_arbitrate(&in, &out);
  return out;
}

static bool nothing_at_all(tg_button_outputs o) {
  return !o.close_menu && !o.menu_foreground && !o.close_setup &&
         !o.close_maintenance && !o.request_setup_open && !o.open_menu &&
         !o.next_app && !o.panic;
}

static const tg_button_action ALL_KEYS[4] = {
    TG_BUTTON_NONE, TG_BUTTON_NEXT_APP, TG_BUTTON_PANIC,
    TG_BUTTON_OPEN_MAINTENANCE};

/* ------------------------------------------------------------------------
 * 1. Underhållsfönstret öppnas BARA från enheten.
 * ---------------------------------------------------------------------- */
static void test_no_input_ever_opens_the_maintenance_window(void) {
  /* Den starkaste formen av regeln: skiljedomen har ingen utdata som öppnar
   * underhållsfönstret. Fönstret nås enbart genom ett tryck på UPDATE-raden
   * inne i menyn, och menyn kan bara öppnas av ett FULLBORDAT tresekunders
   * håll på den fysiska knappen. Svepet nedan bevisar det andra ledet: över
   * alla 64 möjliga lägen är den ENDA vägen till ett fönster — menyn eller
   * setupbegäran — en OPEN_MAINTENANCE-handling, alltså ett verkligt håll.
   * Ingen kombination av tillstånd som ett skript eller en annan task kan
   * sätta (notis, fönster, meny) öppnar någonting av sig själv. */
  for (int k = 0; k < 4; k++) {
    for (int bits = 0; bits < 16; bits++) {
      tg_button_outputs o =
          arb(ALL_KEYS[k], bits & 1, bits & 2, bits & 4, bits & 8);
      if (o.open_menu || o.request_setup_open)
        check("only a completed physical hold can open anything",
              ALL_KEYS[k] == TG_BUTTON_OPEN_MAINTENANCE);
    }
  }
  /* Och utan knapphandling händer ingenting alls, hur glaset än ser ut. */
  for (int bits = 0; bits < 16; bits++) {
    tg_button_outputs o = arb(TG_BUTTON_NONE, bits & 1, bits & 2, bits & 4,
                              bits & 8);
    check("an idle tick never opens a window",
          !o.open_menu && !o.request_setup_open);
  }
}

/* ------------------------------------------------------------------------
 * 2. Varje släpp FÖRE tre sekunder stänger ett öppet fönster.
 * ---------------------------------------------------------------------- */
static void test_every_release_before_three_seconds_closes_the_window(void) {
  /* Hårdvaruläxan 2026-08-16: ett ~2 s tryck panikade i stället för att
   * stänga, och underhållsfönstret satt kvar i tio minuter. Nödutgången får
   * aldrig hänga på att hinna UTANFÖR panikfönstret. */
  tg_button_outputs mid = arb(TG_BUTTON_PANIC, false, true, false, false);
  check("a ~2 s release closes the maintenance window", mid.close_maintenance);
  check("a ~2 s release never panics instead", !mid.panic);
  check("a ~2 s release never switches app", !mid.next_app);

  tg_button_outputs tap = arb(TG_BUTTON_NEXT_APP, false, true, false, false);
  check("a short tap closes the maintenance window", tap.close_maintenance);
  check("a short tap never switches app behind the window", !tap.next_app);

  /* Samma nödutgång i setupfönstret. */
  tg_button_outputs s_mid = arb(TG_BUTTON_PANIC, true, false, false, false);
  check("a ~2 s release closes the setup window", s_mid.close_setup);
  check("a ~2 s release in setup never panics", !s_mid.panic);
  tg_button_outputs s_tap = arb(TG_BUTTON_NEXT_APP, true, false, false, false);
  check("a short tap closes the setup window", s_tap.close_setup);
  check("a short tap in setup never switches app", !s_tap.next_app);
}

/* ------------------------------------------------------------------------
 * 3. STARTING äger knappen också.
 * ---------------------------------------------------------------------- */
static void test_starting_owns_the_button(void) {
  /* Första ledet, ur den delade fasregeln: STARTING äger indata. Utan den här
   * raden vore invarianten bara ett påstående om en bool som testet självt
   * satte. */
  check("STARTING owns the button",
        tg_wifi_setup_owns_input(TG_WIFI_PHASE_STARTING));
  /* Andra ledet: medan AP:n kommer upp får det UTLÖSANDE släppet aldrig bli
   * appväxling eller panik. request_close() ignorerar STARTING på tjänstens
   * sida, så nödutgången finns kvar när fönstret väl kan stängas. */
  for (int k = 1; k < 3; k++) { /* NEXT_APP och PANIC */
    tg_button_outputs o = arb(ALL_KEYS[k], true, false, false, false);
    check("no app switch while the AP is coming up", !o.next_app);
    check("no panic while the AP is coming up", !o.panic);
    check("the release asks the setup window to close", o.close_setup);
  }
  /* Ett fullbordat håll i setupfönstret gör ingenting alls — fönstret är
   * redan det man ville nå. */
  tg_button_outputs hold = arb(TG_BUTTON_OPEN_MAINTENANCE, true, false, false,
                               false);
  check("a completed hold inside the setup window does nothing",
        nothing_at_all(hold));
}

/* ------------------------------------------------------------------------
 * 4. Ett fullbordat håll i underhållsfönstret BEGÄR setupfönstret.
 * ---------------------------------------------------------------------- */
static void test_completed_hold_requests_the_setup_window(void) {
  tg_button_outputs o =
      arb(TG_BUTTON_OPEN_MAINTENANCE, false, true, false, false);
  check("a completed hold asks for the setup window", o.request_setup_open);
  /* Överlämningen är en BEGÄRAN: setupvaktens window_open äger port 80 och
   * väntar ut OTA:ns httpd-stopp. Stängde skiljedomen fönstret här blev
   * portbytet en kapplöpning mellan två vakter som pollar var 500:e ms. */
  check("the arbitration never closes the maintenance window itself",
        !o.close_maintenance);
  check("the handover opens no menu", !o.open_menu);
}

/* ------------------------------------------------------------------------
 * 5. Menyn stänger sig så fort ett riktigt fönster äger glaset — FÖRE kedjan.
 * ---------------------------------------------------------------------- */
static void test_menu_yields_to_every_window(void) {
  /* Alla tre ägarna, var för sig, utan någon knapphändelse: det här är den
   * ASYNKRONA vägen. Notisen annonseras av maintenance_ui_task och
   * setupfönstret öppnar sig självt efter 90 s utan adress — ingendera har en
   * knapphändelse att hänga beslutet på, och ingendera gick att testa alls
   * innan skiljedomen blev en ren funktion. */
  tg_button_outputs by_notice = arb(TG_BUTTON_NONE, false, false, true, true);
  check("an arriving notice closes the menu", by_notice.close_menu);
  check("a menu under a notice is never held foregrounded",
        !by_notice.menu_foreground);

  tg_button_outputs by_setup = arb(TG_BUTTON_NONE, true, false, false, true);
  check("a self-opening setup window closes the menu", by_setup.close_menu);
  check("a menu under the setup window is never held foregrounded",
        !by_setup.menu_foreground);

  tg_button_outputs by_maint = arb(TG_BUTTON_NONE, false, true, false, true);
  check("an open maintenance window closes the menu", by_maint.close_menu);
  check("a menu under the maintenance window is never held foregrounded",
        !by_maint.menu_foreground);

  /* ORDNINGEN, och det är den som är svår att se: stängningen sker FÖRE
   * knappkedjan, så kedjan läser den STÄNGDA menyn. Diskriminanten är att
   * händelsen går vidare till kedjan i stället för att ätas av menygrenen —
   * det är precis vad "landa på det man faktiskt ser" betyder. */
  tg_button_outputs ordered =
      arb(TG_BUTTON_NEXT_APP, false, false, true, true);
  check("the close lands on the same tick the notice takes over",
        ordered.close_menu);
  check("the key chain sees the already-closed menu, not the open one",
        ordered.next_app);

  /* Samma sak med setupfönstret: menyn stänger OCH kedjan når fönstret. Före
   * fixen åt menygrenen händelsen och stängde ett fönster som inte syntes. */
  tg_button_outputs ordered_setup =
      arb(TG_BUTTON_NEXT_APP, true, false, false, true);
  check("the menu closes when the setup window takes over",
        ordered_setup.close_menu);
  check("and the same release still reaches the visible window",
        ordered_setup.close_setup);
}

/* ------------------------------------------------------------------------
 * 6. Menyn hävdar sitt läge överst VARJE tick när inget fönster äger glaset.
 * ---------------------------------------------------------------------- */
static void test_menu_reasserts_foreground_every_tick(void) {
  /* NO NETWORK-sidan ritar om sin nedräkning varje sekund och lyfter sig då,
   * så en meny som bara lyftes vid öppning begravdes inom en sekund — i precis
   * det läge där WIFI-raden behövs mest. Tio tomma tickar i rad måste alla
   * lyfta; en engångslyftning skulle bara ge den första. */
  for (int tick = 0; tick < 10; tick++) {
    tg_button_outputs o = arb(TG_BUTTON_NONE, false, false, false, true);
    check("every idle tick re-asserts the menu's foreground",
          o.menu_foreground);
    check("an idle tick never closes the menu by itself", !o.close_menu);
  }
  /* Och aldrig när menyn är stängd — lyftet är inget att göra i tomma luften. */
  tg_button_outputs closed = arb(TG_BUTTON_NONE, false, false, false, false);
  check("a closed menu is never foregrounded", !closed.menu_foreground);
  check("a closed menu on an idle tick does nothing at all",
        nothing_at_all(closed));
}

/* ------------------------------------------------------------------------
 * 7. Hållet gör ingenting alls medan UPDATE READY syns.
 * ---------------------------------------------------------------------- */
static void test_hold_does_nothing_under_the_notice(void) {
  /* Takeovern äger glaset utan att vara ett öppet underhållsfönster, så hållet
   * nådde ända fram och öppnade menyn BAKOM notisen: open=true, inget syntes,
   * och den dök upp senare när notisen gick bort. */
  tg_button_outputs o =
      arb(TG_BUTTON_OPEN_MAINTENANCE, false, false, true, false);
  check("a completed hold under the notice does nothing at all",
        nothing_at_all(o));

  /* Villkoret sitter på just DEN grenen: kort tryck och panik ska bete sig
   * precis som förut medan notisen syns. Notisen har sina egna svar
   * (UPDATE-pillret och LATER, med fingret). */
  tg_button_outputs tap = arb(TG_BUTTON_NEXT_APP, false, false, true, false);
  check("a short tap still switches app under the notice", tap.next_app);
  tg_button_outputs mid = arb(TG_BUTTON_PANIC, false, false, true, false);
  check("a mid-hold release still panics under the notice", mid.panic);
}

/* ------------------------------------------------------------------------
 * 8. VARJE KEY3-händelse stänger menyn.
 * ---------------------------------------------------------------------- */
static void test_any_key_event_closes_the_menu(void) {
  /* Menyn ärver fönstrens nödutgång. Svepet täcker alla åtta fönsterlägen så
   * att ingen kombination kan lämna menyn kvar med en händelse i handen. */
  for (int k = 1; k < 4; k++) { /* allt utom NONE */
    for (int bits = 0; bits < 8; bits++) {
      tg_button_outputs o =
          arb(ALL_KEYS[k], bits & 1, bits & 2, bits & 4, true);
      check("any KEY3 event closes the open menu", o.close_menu);
    }
  }
  /* Inga sidoeffekter av att stänga menyn på egen hand: ingen panik och ingen
   * appväxling bakom den, exakt som med de två fönstren. Och menyn öppnar
   * aldrig sig själv igen ovanpå sig själv. */
  for (int k = 1; k < 4; k++) {
    tg_button_outputs o = arb(ALL_KEYS[k], false, false, false, true);
    check("no panic fires while the menu is up", !o.panic);
    check("no app switch happens behind the menu", !o.next_app);
    check("the menu never reopens on top of itself", !o.open_menu);
  }
  /* En tom tick lämnar den däremot i fred. */
  tg_button_outputs idle = arb(TG_BUTTON_NONE, false, false, false, true);
  check("an idle tick leaves the menu open", !idle.close_menu);
}

/* ------------------------------------------------------------------------
 * Kedjans två vanliga fall, så en mutation som bryter dem inte kan gömma sig
 * bakom att alla invarianttester råkar handla om fönster.
 * ---------------------------------------------------------------------- */
static void test_plain_glass(void) {
  tg_button_outputs tap = arb(TG_BUTTON_NEXT_APP, false, false, false, false);
  check("a short tap on plain glass switches app", tap.next_app);
  check("a short tap on plain glass does nothing else",
        !tap.panic && !tap.open_menu && !tap.close_menu);

  tg_button_outputs mid = arb(TG_BUTTON_PANIC, false, false, false, false);
  check("a mid-hold release on plain glass panics", mid.panic);
  check("a mid-hold release on plain glass does nothing else",
        !mid.next_app && !mid.open_menu);

  tg_button_outputs hold =
      arb(TG_BUTTON_OPEN_MAINTENANCE, false, false, false, false);
  check("a completed hold on plain glass opens the menu", hold.open_menu);
  check("a completed hold on plain glass does nothing else",
        !hold.next_app && !hold.panic && !hold.request_setup_open);
}

static void test_outputs_are_always_fully_written(void) {
  /* Anroparen får aldrig läsa skräp: varje fält skrivs varje gång. Svepet ser
   * hela tabellen, så ett tidigt return skulle synas här. */
  for (int k = 0; k < 4; k++) {
    for (int bits = 0; bits < 16; bits++) {
      tg_button_outputs o =
          arb(ALL_KEYS[k], bits & 1, bits & 2, bits & 4, bits & 8);
      /* Bool-fälten är antingen 0 eller 1 — aldrig 0xAA. */
      check("every output field is written",
            (o.close_menu | o.menu_foreground | o.close_setup |
             o.close_maintenance | o.request_setup_open | o.open_menu |
             o.next_app | o.panic) <= 1);
    }
  }
}

int main(void) {
  test_no_input_ever_opens_the_maintenance_window();
  test_every_release_before_three_seconds_closes_the_window();
  test_starting_owns_the_button();
  test_completed_hold_requests_the_setup_window();
  test_menu_yields_to_every_window();
  test_menu_reasserts_foreground_every_tick();
  test_hold_does_nothing_under_the_notice();
  test_any_key_event_closes_the_menu();
  test_plain_glass();
  test_outputs_are_always_fully_written();
  if (failures) {
    printf("%d FAILED\n", failures);
    return 1;
  }
  printf("OK: KEY3:s skiljedom håller alla åtta invarianterna\n");
  return 0;
}
