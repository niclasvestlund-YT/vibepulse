/*
 * Presenterns tester — ren C, ingen LVGL. Exakta stopp, en handräknad
 * interpolationspunkt, gråskalan, tile-semantiken (streck vs ärligt noll)
 * och legend-arrayen. Samma metod som test_usage_presenter.c.
 */
#include <stdio.h>
#include <string.h>

#include "../components/app_tokens/max_tracker_presenter.h"

static int failures = 0;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static int rgb_eq(tk_mt_rgb a, uint8_t r, uint8_t g, uint8_t b) {
  return a.r == r && a.g == g && a.b == b;
}

int main(void) {
  /* --- tk_mt_cell_rgb: exakta stopp --- */

  check("claude 0 = 0c0e11",
        rgb_eq(tk_mt_cell_rgb(false, 0), 0x0c, 0x0e, 0x11));
  check("claude 30 = 2c1a12",
        rgb_eq(tk_mt_cell_rgb(false, 30), 0x2c, 0x1a, 0x12));
  check("claude 60 = 6c3a22",
        rgb_eq(tk_mt_cell_rgb(false, 60), 0x6c, 0x3a, 0x22));
  check("claude 85 = D97757",
        rgb_eq(tk_mt_cell_rgb(false, 85), 217, 119, 87));
  check("claude 99 = F09470",
        rgb_eq(tk_mt_cell_rgb(false, 99), 240, 148, 112));
  check("claude 100 = exakt röd FF2D1F",
        rgb_eq(tk_mt_cell_rgb(false, 100), 255, 45, 31));

  check("codex 0 = 0c0e13",
        rgb_eq(tk_mt_cell_rgb(true, 0), 0x0c, 0x0e, 0x13));
  check("codex 30 = 1a1c34",
        rgb_eq(tk_mt_cell_rgb(true, 30), 0x1a, 0x1c, 0x34));
  check("codex 60 = 3a3f7a",
        rgb_eq(tk_mt_cell_rgb(true, 60), 0x3a, 0x3f, 0x7a));
  check("codex 85 = 6F78FF",
        rgb_eq(tk_mt_cell_rgb(true, 85), 111, 120, 255));
  check("codex 99 = 969EFF",
        rgb_eq(tk_mt_cell_rgb(true, 99), 150, 158, 255));
  check("codex 100 = exakt röd FF2D1F (delad tripplett)",
        rgb_eq(tk_mt_cell_rgb(true, 100), 255, 45, 31));

  /* 99 får ALDRIG bli den exakta röda tripletten — bara ett specialfall
   * för pct == 100 garanterar det, avrundning i 99->100-segmentet skulle
   * annars kunna glida mycket nära rött. */
  check("claude 99 är inte röd",
        !rgb_eq(tk_mt_cell_rgb(false, 99), 255, 45, 31));
  check("codex 99 är inte röd",
        !rgb_eq(tk_mt_cell_rgb(true, 99), 255, 45, 31));

  /* --- Handräknade interpolationspunkter --- */

  /* claude 45: mitt i 30->60. (44,26,18) -> (108,58,34), t=0.5 exakt. */
  check("claude 45 halvvägs 30->60",
        rgb_eq(tk_mt_cell_rgb(false, 45), 76, 42, 26));

  /* claude 92: mitt i 85->99. (217,119,87) -> (240,148,112), t=0.5 exakt,
   * varje kanaldelta är udda så halva vägen landar på .5 — lround rundar
   * bort från noll: 228.5->229, 133.5->134, 99.5->100. */
  check("claude 92 halvvägs 85->99, rundar bort från noll",
        rgb_eq(tk_mt_cell_rgb(false, 92), 229, 134, 100));

  /* codex 15: mitt i 0->30. (12,14,19) -> (26,28,52), t=0.5 exakt;
   * blåkanalen 19+0.5*(52-19)=35.5 -> lround 36. */
  check("codex 15 halvvägs 0->30, rundar bort från noll",
        rgb_eq(tk_mt_cell_rgb(true, 15), 19, 21, 36));

  /* --- Defensiv klampning utanför kontraktets 0..100 --- */

  check("pct under 0 klampas till 0",
        rgb_eq(tk_mt_cell_rgb(false, -5), 0x0c, 0x0e, 0x11));
  check("pct över 100 klampas till exakt röd",
        rgb_eq(tk_mt_cell_rgb(false, 150), 255, 45, 31));

  /* --- tk_mt_gray_rgb: exakta nivåer --- */

  check("grå nivå 0 = 14171c", rgb_eq(tk_mt_gray_rgb(0), 0x14, 0x17, 0x1c));
  check("grå nivå 1 = 1d222a", rgb_eq(tk_mt_gray_rgb(1), 0x1d, 0x22, 0x2a));
  check("grå nivå 2 = 293039", rgb_eq(tk_mt_gray_rgb(2), 0x29, 0x30, 0x39));
  check("grå nivå under 0 klampas till 0",
        rgb_eq(tk_mt_gray_rgb(-1), 0x14, 0x17, 0x1c));
  check("grå nivå över 2 klampas till 2",
        rgb_eq(tk_mt_gray_rgb(5), 0x29, 0x30, 0x39));

  /* --- Legend --- */

  check("legend har 5 procent",
        TK_MT_LEGEND_PCTS[0] == 10 && TK_MT_LEGEND_PCTS[1] == 40 &&
        TK_MT_LEGEND_PCTS[2] == 70 && TK_MT_LEGEND_PCTS[3] == 92 &&
        TK_MT_LEGEND_PCTS[4] == 100);

  /* --- tk_mt_tiles --- */

  tk_max_tracker t;
  memset(&t, 0, sizeof t);
  t.coding_streak_days = 47;
  t.claude.has_avg = true;
  t.claude.avg_peak_pct = 84.0;
  t.claude.max_weeks_streak = 6;
  t.claude.max_weeks = 6;
  t.claude.max_days = 12;
  t.codex.has_avg = true;
  t.codex.avg_peak_pct = 62.0;
  t.codex.max_weeks_streak = 9;
  t.codex.max_weeks = 9;
  t.codex.max_days = 30;

  tk_mt_tile out[4];
  tk_mt_tiles(&t, false, out);
  check("claude streak värde", strcmp(out[0].value, "47") == 0);
  check("claude streak enhet", strcmp(out[0].unit, "DAYS") == 0);
  check("claude max weeks streak värde", strcmp(out[1].value, "6") == 0);
  check("claude max weeks streak tom enhet", strcmp(out[1].unit, "") == 0);
  check("claude avg peak värde", strcmp(out[2].value, "84") == 0);
  check("claude avg peak enhet", strcmp(out[2].unit, "%") == 0);
  check("claude max days värde", strcmp(out[3].value, "12") == 0);
  check("claude max days tom enhet", strcmp(out[3].unit, "") == 0);

  tk_mt_tiles(&t, true, out);
  check("codex läser egen provider (max weeks streak 9, inte claudes 6)",
        strcmp(out[1].value, "9") == 0);
  check("codex avg peak avrundad från 62.0", strcmp(out[2].value, "62") == 0);
  check("codex avg peak enhet", strcmp(out[2].unit, "%") == 0);
  check("codex max days värde", strcmp(out[3].value, "30") == 0);
  check("streak är delad mellan providers (samma som claude)",
        strcmp(out[0].value, "47") == 0);

  /* Saknad bas: streak okänd (-1) och ingen avg-data. */
  tk_max_tracker missing;
  memset(&missing, 0, sizeof missing);
  missing.coding_streak_days = -1;
  missing.claude.has_avg = false;
  missing.claude.max_weeks_streak = 0;
  missing.claude.max_days = 0;

  tk_mt_tiles(&missing, false, out);
  check("okänd streak -> streck", strcmp(out[0].value, "\xe2\x80\x93") == 0);
  check("okänd streak -> tom enhet", strcmp(out[0].unit, "") == 0);
  check("ingen avg -> streck", strcmp(out[2].value, "\xe2\x80\x93") == 0);
  check("ingen avg -> tom enhet", strcmp(out[2].unit, "") == 0);
  check("max weeks streak noll är ärligt noll, inte streck",
        strcmp(out[1].value, "0") == 0);
  check("max days noll är ärligt noll, inte streck",
        strcmp(out[3].value, "0") == 0);

  /* Defensiv klampning: negativa aggregat (bör aldrig komma från parsern,
   * som redan begränsar till [0,999], men presentatorn ska ändå inte visa
   * ett negativt tal om något uppströms ändå bryter kontraktet). */
  tk_max_tracker malformed;
  memset(&malformed, 0, sizeof malformed);
  malformed.coding_streak_days = 5;
  malformed.claude.has_avg = true;
  malformed.claude.avg_peak_pct = -10.0;
  malformed.claude.max_weeks_streak = -5;
  malformed.claude.max_days = -3;

  tk_mt_tiles(&malformed, false, out);
  check("negativ max weeks streak klampas till 0",
        strcmp(out[1].value, "0") == 0);
  check("negativ max days klampas till 0", strcmp(out[3].value, "0") == 0);
  check("negativ avg klampas till 0", strcmp(out[2].value, "0") == 0);

  /* Nollpekare kraschar inte. */
  tk_mt_tiles(NULL, false, out);
  tk_mt_tiles(&t, false, NULL);

  if (failures == 0) {
    printf("OK: alla max-tracker-presenter-tester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
