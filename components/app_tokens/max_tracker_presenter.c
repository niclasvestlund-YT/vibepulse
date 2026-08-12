#include "max_tracker_presenter.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

const int TK_MT_LEGEND_PCTS[5] = {10, 40, 70, 92, 100};

typedef struct {
  int pct;
  tk_mt_rgb rgb;
} tk_mt_stop;

/*
 * Claude: (0)#0c0e11 (30)#2c1a12 (60)#6c3a22 (85)#D97757 (99)#F09470.
 * pct 100 listas INTE här — det hanteras som specialfall i
 * tk_mt_cell_rgb innan interpolationen någonsin körs (se header).
 */
static const tk_mt_stop CLAUDE_STOPS[] = {
    {0, {0x0c, 0x0e, 0x11}},  {30, {0x2c, 0x1a, 0x12}},
    {60, {0x6c, 0x3a, 0x22}}, {85, {0xD9, 0x77, 0x57}},
    {99, {0xF0, 0x94, 0x70}},
};

/* Codex: (0)#0c0e13 (30)#1a1c34 (60)#3a3f7a (85)#6F78FF (99)#969EFF. */
static const tk_mt_stop CODEX_STOPS[] = {
    {0, {0x0c, 0x0e, 0x13}},  {30, {0x1a, 0x1c, 0x34}},
    {60, {0x3a, 0x3f, 0x7a}}, {85, {0x6F, 0x78, 0xFF}},
    {99, {0x96, 0x9E, 0xFF}},
};

static const tk_mt_rgb MAX_RED = {0xFF, 0x2D, 0x1F};

static const tk_mt_rgb GRAY_LEVELS[3] = {
    {0x14, 0x17, 0x1c},
    {0x1d, 0x22, 0x2a},
    {0x29, 0x30, 0x39},
};

static uint8_t lerp_channel(uint8_t from, uint8_t to, int pct, int lo,
                            int hi) {
  if (hi <= lo) return from;
  double t = (double)(pct - lo) / (double)(hi - lo);
  double value = (double)from + t * ((double)to - (double)from);
  long rounded = lround(value);
  if (rounded < 0) rounded = 0;
  if (rounded > 255) rounded = 255;
  return (uint8_t)rounded;
}

static tk_mt_rgb interpolate(const tk_mt_stop *stops, size_t count,
                             int pct) {
  size_t i = 0;
  while (i + 1 < count && pct > stops[i + 1].pct) i++;
  const tk_mt_stop *lo = &stops[i];
  const tk_mt_stop *hi = &stops[i + 1 < count ? i + 1 : i];
  tk_mt_rgb out;
  out.r = lerp_channel(lo->rgb.r, hi->rgb.r, pct, lo->pct, hi->pct);
  out.g = lerp_channel(lo->rgb.g, hi->rgb.g, pct, lo->pct, hi->pct);
  out.b = lerp_channel(lo->rgb.b, hi->rgb.b, pct, lo->pct, hi->pct);
  return out;
}

tk_mt_rgb tk_mt_cell_rgb(bool codex, int pct) {
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  if (pct == 100) return MAX_RED; /* specialfall — se header för motivering */

  if (codex) {
    return interpolate(CODEX_STOPS,
                       sizeof CODEX_STOPS / sizeof CODEX_STOPS[0], pct);
  }
  return interpolate(CLAUDE_STOPS,
                     sizeof CLAUDE_STOPS / sizeof CLAUDE_STOPS[0], pct);
}

tk_mt_rgb tk_mt_gray_rgb(int lvl) {
  if (lvl < 0) lvl = 0;
  if (lvl > 2) lvl = 2;
  return GRAY_LEVELS[lvl];
}

static void set_dash(tk_mt_tile *tile) {
  snprintf(tile->value, sizeof tile->value, "–");
  tile->unit[0] = '\0';
}

/* Formaterar ett ärligt heltal (aldrig ett streck). Klampas defensivt till
 * [0,999] — parsern begränsar redan aggregaten till samma intervall
 * (TK_MT_AGGREGATE_MAX), men presentatorn ska ändå aldrig kunna skriva ett
 * tal utanför det intervallet till den lilla buffertn om något uppströms
 * ändå bryter kontraktet. Övre klampen håller dessutom snprintf-bredden
 * bevisbart inom tile->value (8 byte) för kompilatorn. */
static void set_int(tk_mt_tile *tile, int value, const char *unit) {
  if (value < 0) value = 0;
  else if (value > 999) value = 999;
  snprintf(tile->value, sizeof tile->value, "%d", value);
  snprintf(tile->unit, sizeof tile->unit, "%s", unit);
}

void tk_mt_tiles(const tk_max_tracker *t, bool codex, tk_mt_tile out[4]) {
  if (!out) return;
  memset(out, 0, sizeof(tk_mt_tile) * 4);
  if (!t) return;

  const tk_mt_provider *p = codex ? &t->codex : &t->claude;

  if (t->coding_streak_days < 0) {
    set_dash(&out[0]);
  } else {
    set_int(&out[0], t->coding_streak_days, "DAYS");
  }

  set_int(&out[1], p->max_weeks_streak, "");

  if (p->has_avg) {
    set_int(&out[2], (int)lround(p->avg_peak_pct), "%");
  } else {
    set_dash(&out[2]);
  }

  set_int(&out[3], p->max_days, "");
}
