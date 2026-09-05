/*
 * KEY3 som en RESA, inte som en tabell.
 *
 * test_key3_arbitration.c pinnar åtta invarianter var för sig, och det är rätt
 * form för dem: en mutation ska slå i exakt ett test. Men varje ruta i den
 * tabellen ställs upp för hand, och en tabell där alla rutor är gröna säger
 * ingenting om ÖVERGÅNGARNA mellan dem. Skiljedomen är ren, så tillståndet bor
 * hos värden — och det är just där en flödesbugg får plats: utdata som inte
 * matas tillbaka, ett fönster som stängs men vars flagga står kvar, en meny som
 * öppnas i ett läge den aldrig skulle nått.
 *
 * Den här filen kör därför en enda sammanhängande färd genom hela flödet och
 * MATAR TILLBAKA varje utdata som nästa ticks indata, precis som main.c gör.
 * Världen ändras dessutom av saker knappen inte styr — setupfönstret öppnar sig
 * självt efter 90 s utan adress, notisen annonseras av en annan task, fönster
 * tar slut — och de injiceras som yttre händelser mitt i resan, för det är den
 * verkliga ordningen och det är där de tre buggarna i #72 faktiskt satt.
 *
 * Vad den INTE täcker, med flit: fingret. Radernas tryck går genom
 * torget_settings_click_row(), inte genom skiljedomen, och att härma det här
 * hade blivit en andra kopia av menyns logik som kan glida isär från den —
 * exakt den QA-bakdörr platform/settings_menu.h säger nej till. Resan nedan är
 * KNAPPENS resa. Touchen bevisas i simulatorns pinnade rasterbilder.
 */

#include <stdio.h>
#include <string.h>

#include "../platform/button_arbitration.h"

static int failures;
static int steps;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

/*
 * Världen som main.c ser den. Skiljedomen äger inget av detta — den läser det
 * och säger vad värden ska göra; det här är värdens halva, modellerad så
 * troget som en ren fil kan.
 */
typedef struct {
  bool menu_open;
  bool setup_owns_input;
  bool maintenance_open;
  bool notice_visible;
} world;

/* Ett tick: skiljedomen får världen, och världen uppdateras av svaret precis
 * som main.c:s verkställande gör. Returnerar utdata så steget kan granskas. */
static tg_button_outputs tick(world *w, tg_button_action key) {
  const tg_button_inputs in = {
      .key = key,
      .setup_owns_input = w->setup_owns_input,
      .maintenance_open = w->maintenance_open,
      .notice_visible = w->notice_visible,
      .menu_open = w->menu_open,
  };
  tg_button_outputs out;
  memset(&out, 0xAA, sizeof out); /* oskriven utdata ska synas som fel */
  tg_button_arbitrate(&in, &out);

  /* Verkställandet, i main.c:s ordning. */
  if (out.close_menu) w->menu_open = false;
  if (out.open_menu) w->menu_open = true;
  /* close_setup/close_maintenance är BEGÄRAN till vakter som äger sina egna
   * fönster; de svarar inte samma tick. Resan nedan låter dem svara som
   * yttre händelser, vilket är vad som faktiskt händer. */
  steps++;
  return out;
}

static bool nothing_at_all(tg_button_outputs o) {
  return !o.close_menu && !o.menu_foreground && !o.close_setup &&
         !o.close_maintenance && !o.request_setup_open && !o.open_menu &&
         !o.next_app && !o.panic;
}

/* ------------------------------------------------------------------------
 * Resan: en panel som startar utan nät, hittar ett, och uppdateras.
 * ---------------------------------------------------------------------- */
static void the_journey(void) {
  world w = {0};
  tg_button_outputs o;

  /* 1. Ren glasyta. Ett kort tryck byter app — inget annat. */
  o = tick(&w, TG_BUTTON_NEXT_APP);
  check("1 kort tryck byter app", o.next_app);
  check("1 och rör inget fönster",
        !o.open_menu && !o.close_setup && !o.close_maintenance);

  /* 2. Tresekundershållet öppnar menyn. Panelen har ingen adress ännu; det
   *    syns i menyn (UPDATE tonas ner), inte i skiljedomen. */
  o = tick(&w, TG_BUTTON_OPEN_MAINTENANCE);
  check("2 hållet öppnar menyn", o.open_menu);
  check("2 hållet öppnar ALDRIG underhållsfönstret direkt",
        !o.request_setup_open);
  check("2 menyn är nu uppe", w.menu_open);

  /* 3. NO NETWORK-sidan ritar om sig varje sekund och lyfter sig själv. Den
   *    äger inte knappen, så menyn ska ligga kvar OCH hävda sitt läge. Utan
   *    det begravdes menyn inom en sekund — #72:s andra bugg. */
  for (int i = 0; i < 3; i++) {
    o = tick(&w, TG_BUTTON_NONE);
    check("3 menyn hävdar sitt läge varje tick", o.menu_foreground);
    check("3 och stängs inte av en tom tick", !o.close_menu);
  }
  check("3 menyn står kvar", w.menu_open);

  /* 4. YTTRE: 90 s utan adress gått — setupfönstret öppnar sig SJÄLVT.
   *    Menyn ska stänga sig på samma tick fönstret tar över. */
  w.setup_owns_input = true;
  o = tick(&w, TG_BUTTON_NONE);
  check("4 setupfönstret stänger menyn", o.close_menu);
  check("4 menyn hålls inte kvar överst", !o.menu_foreground);
  check("4 menyn är borta", !w.menu_open);

  /* 5. Och nästa tryck landar på det man SER, inte på menyn som var uppe. */
  o = tick(&w, TG_BUTTON_NEXT_APP);
  check("5 trycket når setupfönstret", o.close_setup);
  check("5 trycket byter inte app bakom fönstret", !o.next_app);
  check("5 och öppnar ingen meny", !o.open_menu);

  /* 5b. SAMMA TICK: fönstret tar över OCH en knapphändelse kommer. Det är
   *     hela poängen med att stängningen ligger före kedjan — kedjan måste
   *     läsa den STÄNGDA menyn, annars äter menygrenen trycket och det
   *     synliga fönstret svarar inte. Steget finns här för att resan utan
   *     det missade två mutationer som tabelltestet fångade: en resa där
   *     varje övergång är lugn bevisar inte att den bråkiga fungerar. */
  {
    world same = {.menu_open = true, .setup_owns_input = true};
    o = tick(&same, TG_BUTTON_NEXT_APP);
    check("5b menyn stänger på samma tick som övertagandet", o.close_menu);
    check("5b och SAMMA tryck når ändå fönstret", o.close_setup);
    check("5b menyn hålls inte kvar överst", !same.menu_open);

    world same_notice = {.menu_open = true, .notice_visible = true};
    o = tick(&same_notice, TG_BUTTON_OPEN_MAINTENANCE);
    check("5b notisen stänger menyn på samma tick", o.close_menu);
    check("5b och hållet öppnar den inte igen bakom notisen",
          !o.open_menu);
    check("5b menyn är borta, inte återöppnad", !same_notice.menu_open);

    /* Och det skarpaste fallet i hela filen. Notisen tar glaset SAMTIDIGT
     * som ett kort tryck kommer. Menyn ska stängas OCH trycket ska ändå
     * byta app — för menygrenen får inte äta ett tryck åt en meny som
     * användaren just förlorade och aldrig såg. Notisen är dessutom det
     * enda övertagandet där kedjan når menygrenen alls: setup- och
     * underhållsgrenarna ligger före den och skymmer felet. Utan just den
     * här raden gick två mutationer rakt igenom resan medan tabellen tog
     * dem — mätt, inte antaget. */
    world notice_and_tap = {.menu_open = true, .notice_visible = true};
    o = tick(&notice_and_tap, TG_BUTTON_NEXT_APP);
    check("5b notis + tryck: menyn stängs", o.close_menu);
    check("5b notis + tryck: trycket byter ÄNDÅ app", o.next_app);

    world same_maint = {.menu_open = true, .maintenance_open = true};
    o = tick(&same_maint, TG_BUTTON_NEXT_APP);
    check("5b underhållsfönstret stänger menyn på samma tick",
          o.close_menu);
    check("5b och samma tryck stänger fönstret", o.close_maintenance);
  }

  /* 6. YTTRE: vakten stängde fönstret, och ett nät hittades. */
  w.setup_owns_input = false;
  o = tick(&w, TG_BUTTON_OPEN_MAINTENANCE);
  check("6 hållet öppnar menyn igen", o.open_menu);
  check("6 menyn är uppe", w.menu_open);

  /* 7. YTTRE: datorn annonserar ett nyare bygge medan menyn står uppe.
   *    Notisen kommer från maintenance_ui_task — ingen knapphändelse finns
   *    att hänga beslutet på, och utan det här låg menyn kvar med open=true
   *    bakom notisen och dök upp igen på LATER. #72:s första bugg. */
  w.notice_visible = true;
  o = tick(&w, TG_BUTTON_NONE);
  check("7 notisen stänger menyn", o.close_menu);
  check("7 menyn är borta", !w.menu_open);

  /* 8. Hållet gör ingenting alls medan notisen äger glaset — menyn får inte
   *    öppnas bakom den. Kort tryck och panik beter sig som vanligt. */
  o = tick(&w, TG_BUTTON_OPEN_MAINTENANCE);
  check("8 hållet gör ingenting under notisen", nothing_at_all(o));
  check("8 och menyn förblir stängd", !w.menu_open);
  o = tick(&w, TG_BUTTON_NEXT_APP);
  check("8 kort tryck byter fortfarande app under notisen", o.next_app);
  o = tick(&w, TG_BUTTON_PANIC);
  check("8 mellanhållet panikar fortfarande under notisen", o.panic);

  /* 9. YTTRE: användaren svarade notisen med UPDATE-pillret (fingret, inte
   *    knappen — den vägen är den ENDA som öppnar fönstret). */
  w.notice_visible = false;
  w.maintenance_open = true;

  /* 10. Nödutgången: varje släpp FÖRE tre sekunder stänger fönstret. Ett
   *     ~2 s tryck panikade en gång i stället och lämnade fönstret öppet
   *     (hårdvara, 2026-08-16). */
  o = tick(&w, TG_BUTTON_PANIC);
  check("10 ~2 s släpp stänger underhållsfönstret", o.close_maintenance);
  check("10 och panikar inte i stället", !o.panic);

  /* 11. Ett NYTT fullbordat håll inne i fönstret byter till WIFI SETUP —
   *     vägen att lära en panel MED nät ett nytt nät. Det är en BEGÄRAN:
   *     setupvakten äger överlämningen av port 80. */
  o = tick(&w, TG_BUTTON_OPEN_MAINTENANCE);
  check("11 hållet begär setupfönstret", o.request_setup_open);
  check("11 skiljedomen stänger inte OTA-fönstret själv",
        !o.close_maintenance);
  check("11 och öppnar ingen meny ovanpå", !o.open_menu);

  /* 12. YTTRE: överlämningen sker som den gör på riktigt, i TVÅ steg. Att
   *     hoppa rakt till ren glasyta här — som den första versionen gjorde —
   *     lät resan påstå att den prövade övergången underhåll → setup utan
   *     att någonsin ta den: vakten går först till STARTING, där
   *     setup_owns_input är sant, och först därefter stänger OTA-fönstret.
   *     Utan mellansteget hade en felroutad knapp i just det läget aldrig
   *     synts. */
  w.setup_owns_input = true;  /* STARTING: AP:n kommer upp */
  w.maintenance_open = false; /* vakten stängde OTA-fönstret åt oss */

  /* 12b. Under STARTING äger setupvakten knappen. Det utlösande släppet får
   *      aldrig bli appväxling eller panik medan AP/skanning pågår — och
   *      request_close() ignorerar STARTING, så nödutgången finns kvar men
   *      gör ännu ingenting synligt. */
  o = tick(&w, TG_BUTTON_NEXT_APP);
  check("12b STARTING äger knappen", o.close_setup);
  check("12b och trycket byter inte app", !o.next_app);
  check("12b och panikar inte", !o.panic);
  o = tick(&w, TG_BUTTON_OPEN_MAINTENANCE);
  check("12b ett håll under STARTING öppnar ingen meny", !o.open_menu);
  check("12b och begär inte setupfönstret en gång till",
        !o.request_setup_open);

  /* 12c. YTTRE: fönstret öppnades, användaren stängde det. Ren glasyta. */
  w.setup_owns_input = false;

  /* 13. Med allt stängt betyder mellanhållet panik igen — samma gest, annan
   *     mening, och det är hela poängen med en skiljedom. */
  o = tick(&w, TG_BUTTON_PANIC);
  check("13 mellanhållet panikar på ren glasyta", o.panic);
  check("13 och stänger inget fönster",
        !o.close_setup && !o.close_maintenance);

  /* 14. En tom tick på ren glasyta ska göra exakt ingenting. */
  o = tick(&w, TG_BUTTON_NONE);
  check("14 en tom tick på ren glasyta gör ingenting", nothing_at_all(o));

  check("resan besökte alla steg", steps >= 15);
}

/* ------------------------------------------------------------------------
 * Resan får aldrig lämna världen i ett läge som inte går att ta sig ur.
 * ---------------------------------------------------------------------- */
static void no_state_is_a_dead_end(void) {
  /* Varje kombination av världen: finns det ALLTID en knapphändelse som tar
   * användaren vidare? En meny som inte går att stänga, eller ett fönster
   * utan nödutgång, är precis den klass av fel som fick en frisk enhet att
   * se hängd ut i tio minuter. */
  for (int m = 0; m < 2; m++)
    for (int s = 0; s < 2; s++)
      for (int mt = 0; mt < 2; mt++)
        for (int n = 0; n < 2; n++) {
          world w = {.menu_open = m != 0,
                     .setup_owns_input = s != 0,
                     .maintenance_open = mt != 0,
                     .notice_visible = n != 0};
          bool escapable = false;
          /* Kort tryck är den gest som alltid ska finnas till hands. */
          world probe = w;
          tg_button_outputs o = tick(&probe, TG_BUTTON_NEXT_APP);
          if (o.close_menu || o.close_setup || o.close_maintenance ||
              o.next_app)
            escapable = true;
          /* Notisen svaras med fingret, inte med knappen — den är det enda
           * läget där knappen med flit inte gör något, och den har sina
           * egna pillar. Menyn under den stängs ändå av samma tryck. */
          if (w.notice_visible && !w.menu_open && !w.setup_owns_input &&
              !w.maintenance_open)
            escapable = true;
          check("inget läge saknar en väg vidare", escapable);
        }
}

int main(void) {
  the_journey();
  no_state_is_a_dead_end();
  if (failures) {
    printf("%d fel\n", failures);
    return 1;
  }
  printf("OK: KEY3-flödet håller hela resan (%d tick)\n", steps);
  return 0;
}
