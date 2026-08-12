#ifndef MAX_TRACKER_PRESENTER_H
#define MAX_TRACKER_PRESENTER_H

#include <stdbool.h>
#include <stdint.h>

#include "max_tracker.h"

/*
 * Ren presentationskärna för Max Tracker — samma linje som
 * usage_presenter: ingen LVGL, inga sidoeffekter, bara data in och
 * färdiga RGB-trippletter/strängar ut. Renderaren (Task 8) konsumerar
 * detta rakt av och lägger bara till layout och konturen #3d434d.
 */

typedef struct {
  uint8_t r, g, b;
} tk_mt_rgb;

/*
 * Cellfärg för en dag med känd kvot (pct 0..100, klampas defensivt utanför
 * intervallet). Linjär interpolation mellan de fastslagna färgstoppen,
 * avrundat kanal för kanal med lround.
 *
 * pct == 100 är ett SPECIALFALL som returnerar den exakta röda tripletten
 * FF2D1F direkt, INNAN någon interpolation körs — annars skulle
 * avrundningen i 99->100-segmentet kunna landa mycket nära rött (men inte
 * exakt) och göra 99 och 100 omöjliga att skilja åt visuellt. Rött hör
 * bara till en exakt kvotmax.
 */
tk_mt_rgb tk_mt_cell_rgb(bool codex, int pct);

/*
 * Gråfyllning för aktivitet-utan-kvot-dagar (lvl 0..2, klampas defensivt).
 * Konturen (#3d434d) hör till renderaren, inte till presentatorn.
 */
tk_mt_rgb tk_mt_gray_rgb(int lvl);

/* Procenten legend-swatchen i UI:t demonstrerar färgskalan med. */
extern const int TK_MT_LEGEND_PCTS[5];

#define TK_MT_TILE_VALUE_CAP 8
#define TK_MT_TILE_UNIT_CAP 6

typedef struct {
  char value[TK_MT_TILE_VALUE_CAP];
  char unit[TK_MT_TILE_UNIT_CAP];
} tk_mt_tile;

/*
 * out[0] STREAK   — codingStreakDays (delad mellan claude/codex), "DAYS".
 * out[1] MAX WEEKS — max_weeks_streak (konsekutiva maxade ISO-veckor för
 *                    vald provider), ingen enhet.
 * out[2] AVG PEAK  — avg_peak_pct avrundad med lround, "%".
 * out[3] MAX DAYS  — max_days för vald provider, ingen enhet.
 *
 * En SAKNAD bas (streak == -1, has_avg == false) renderas "–" (UTF-8
 * en-dash) med tom enhet. out[1] och out[3] har ingen saknad-status —
 * noll är alltid ett ärligt värde, aldrig ett streck.
 */
void tk_mt_tiles(const tk_max_tracker *t, bool codex, tk_mt_tile out[4]);

#endif
