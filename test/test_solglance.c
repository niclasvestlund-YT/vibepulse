/*
 * Host-side unit tests for the pure firmware core: sv-SE formatting and the
 * ticker. Runs on the Mac with plain clang — no ESP-IDF, no hardware:
 *
 *   cd firmware/test && ./run.sh
 *
 * These pin down the fiddly parts (U+00A0 grouping, integer-scaled ticker
 * split, anchor maths) before the board exists, so the LVGL port only has to
 * paint strings it can already trust.
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../components/torget_fmt/fmt_sv.h"
#include "../components/torget_ticker/ticker.h"

#define NBSP "\xC2\xA0"

static int failures = 0;

static void expect_str(const char *what, const char *got, const char *want) {
  if (strcmp(got, want) != 0) {
    printf("FAIL %s: fick \"%s\", ville ha \"%s\"\n", what, got, want);
    failures++;
  }
}

static void expect_close(const char *what, double got, double want) {
  double diff = got - want;
  if (diff < 0) diff = -diff;
  if (diff > 1e-9) {
    printf("FAIL %s: fick %.12f, ville ha %.12f\n", what, got, want);
    failures++;
  }
}

static void test_grouping(void) {
  char buf[32];
  sv_group_ll(0, buf, sizeof buf);       expect_str("grupp 0", buf, "0");
  sv_group_ll(9, buf, sizeof buf);       expect_str("grupp 9", buf, "9");
  sv_group_ll(100, buf, sizeof buf);     expect_str("grupp 100", buf, "100");
  sv_group_ll(9524, buf, sizeof buf);    expect_str("grupp 9524", buf, "9" NBSP "524");
  sv_group_ll(103147, buf, sizeof buf);  expect_str("grupp 103147", buf, "103" NBSP "147");
  sv_group_ll(170000, buf, sizeof buf);  expect_str("grupp 170000", buf, "170" NBSP "000");
  sv_group_ll(1234567, buf, sizeof buf); expect_str("grupp 1234567", buf, "1" NBSP "234" NBSP "567");
  /* Negativt klampas till noll: enheten kan aldrig ärligt visa minus. */
  sv_group_ll(-42, buf, sizeof buf);     expect_str("grupp -42", buf, "0");
  /* För liten buffert trunkerar säkert i stället för att skriva utanför. */
  char tiny[4];
  sv_group_ll(1234567, tiny, sizeof tiny);
  assert(tiny[sizeof tiny - 1] == '\0' || strlen(tiny) < sizeof tiny);
}

static void test_ticker_split(void) {
  sv_ticker_str s;

  /* Det observerade produktionsfallet: 100,6 kr — en siffra bredare än
   * designens ursprungliga antagande. Svansen är TVÅ siffror sedan
   * 2026-08-06, samma bredd som Översiktens hero. */
  sv_ticker_split(100.6042, &s);
  expect_str("split 100.6042 heltal", s.whole, "100");
  expect_str("split 100.6042 frac", s.frac, "60");
  expect_str("split 100.6042 tail", s.tail, "42");

  /* Midnattsfallet efter fixen: ärlig nolla. */
  sv_ticker_split(0.0, &s);
  expect_str("split 0 heltal", s.whole, "0");
  expect_str("split 0 frac", s.frac, "00");
  expect_str("split 0 tail", s.tail, "00");

  /* FP-fällan splitTicking finns till för: 84.27 får ALDRIG bli "84,26|99". */
  sv_ticker_split(84.27, &s);
  expect_str("split 84.27 frac", s.frac, "27");
  expect_str("split 84.27 tail", s.tail, "00");

  /* Tusentalsgräns med gruppering i heltalsdelen. */
  sv_ticker_split(1000.0, &s);
  expect_str("split 1000 heltal", s.whole, "1" NBSP "000");
  expect_str("split 1000 frac", s.frac, "00");

  /* Avrundning vid sista decimalen: 999.99996 -> 1 000,00|00, inte 999,99|99. */
  sv_ticker_split(999.99996, &s);
  expect_str("split 999.99996 heltal", s.whole, "1" NBSP "000");
  expect_str("split 999.99996 frac", s.frac, "00");
  expect_str("split 999.99996 tail", s.tail, "00");

  /* Negativt och NaN klampas till ärlig nolla. */
  sv_ticker_split(-5.0, &s);
  expect_str("split -5 heltal", s.whole, "0");
  sv_ticker_split(0.0 / 0.0, &s);
  expect_str("split NaN heltal", s.whole, "0");
}

static void test_pct(void) {
  char buf[16];
  sv_pct_1(60.7, buf, sizeof buf);   expect_str("pct 60.7", buf, "60,7");
  sv_pct_1(60.65, buf, sizeof buf);  expect_str("pct 60.65", buf, "60,7"); /* llround halvor uppåt */
  sv_pct_1(100.0, buf, sizeof buf);  expect_str("pct 100", buf, "100,0");
  sv_pct_1(0.0, buf, sizeof buf);    expect_str("pct 0", buf, "0,0");
  sv_pct_1(9.96, buf, sizeof buf);   expect_str("pct 9.96", buf, "10,0");
}

static void test_day_short(void) {
  char buf[16];
  sv_day_short("2026-08-04", buf, sizeof buf); expect_str("dag 4 aug", buf, "4 aug");
  sv_day_short("2026-01-31", buf, sizeof buf); expect_str("dag 31 jan", buf, "31 jan");
  sv_day_short("2026-12-01", buf, sizeof buf); expect_str("dag 1 dec", buf, "1 dec");
  /* Ogiltigt ger streck, aldrig en gissad kalender. */
  sv_day_short("4 aug", buf, sizeof buf);      expect_str("dag trasig", buf, "\xE2\x80\x93");
  sv_day_short("2026-13-05", buf, sizeof buf); expect_str("dag månad 13", buf, "\xE2\x80\x93");
  sv_day_short(NULL, buf, sizeof buf);         expect_str("dag NULL", buf, "\xE2\x80\x93");
}

static void test_ticker(void) {
  sg_ticker t = {0};

  /* Utan data: värdet är 0 och has_data säger åt UI:t att rita streck. */
  assert(t.has_data == 0);
  expect_close("ticker utan data", sg_ticker_value(&t, 123456789), 0.0);

  /* Hämtning 12:00: 45,12 kr, 10,40 kr/tim. Fem minuter senare har den
   * tickat upp exakt 10,40/12. */
  sg_ticker_set(&t, 0, 45.12, 10.40);
  expect_close("ticker vid ankare", sg_ticker_value(&t, 0), 45.12);
  expect_close("ticker +5 min", sg_ticker_value(&t, 300000000LL), 45.12 + 10.40 / 12.0);
  expect_close("ticker +30 s", sg_ticker_value(&t, 30000000LL), 45.12 + 10.40 / 120.0);

  /* Ny hämtning snappar till serverns auktoritativa bas, även bakåt. */
  sg_ticker_set(&t, 30000000LL, 45.15, 9.80);
  expect_close("ticker efter snapp", sg_ticker_value(&t, 30000000LL), 45.15);

  /* Natten: bas 0, takt 0 — står stilla på ärlig nolla. */
  sg_ticker_set(&t, 0, 0.0, 0.0);
  expect_close("ticker natt", sg_ticker_value(&t, 3600000000LL), 0.0);

  /* Glitchad payload med negativ takt klampas: tickern går aldrig baklänges. */
  sg_ticker_set(&t, 0, 10.0, -5.0);
  expect_close("ticker negativ takt", sg_ticker_value(&t, 3600000000LL), 10.0);

  /* Klocka som råkar backa ger ankarvärdet, inte ett hopp bakåt. */
  sg_ticker_set(&t, 1000000LL, 20.0, 12.0);
  expect_close("ticker bakåtklocka", sg_ticker_value(&t, 0), 20.0);
}

int main(void) {
  test_grouping();
  test_ticker_split();
  test_pct();
  test_day_short();
  test_ticker();
  if (failures == 0) {
    printf("OK: alla solglance-kärntester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
