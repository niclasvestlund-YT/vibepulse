#ifndef MAX_TRACKER_H
#define MAX_TRACKER_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Speglar Mac-tjänstens /api/max-tracker (kontrakt v1) — tät form: datumen
 * är redan upplösta server-side, enheten får bara ett fönster med fast
 * längd (ingen datumsträng på tråden, indexpositionen bär dagen).
 *
 * TK_MT_WEEKS ISO-veckor bakåt, TK_MT_DAYS = TK_MT_WEEKS * 7 dagar, index 0
 * äldsta måndagen, index TK_MT_DAYS-1 innevarande ISO-veckas söndag. Idag
 * är den sista icke-vadderade cellen, inte alltid index TK_MT_DAYS-1 i sig
 * — dagar efter idag i samma sista vecka är vaddering, [-1, -1] precis
 * som en frånvarande dag.
 */
#define TK_MT_WEEKS 20
#define TK_MT_DAYS (TK_MT_WEEKS * 7)

/*
 * pct: -1..100, -1 = ingen kvotdata den dagen (ärlig frånvaro), 100 är
 * reserverat för en exakt kvotmax.
 * lvl: -1..2, oberoende fält satt av servern från volym — -1 = inaktiv dag,
 * 0-2 = tercil för aktivitet-utan-kvot-dagar (backfill). lvl kan vara 0-2
 * även när pct >= 0; bara rendering avgör att grått gäller när pct == -1.
 */
typedef struct {
  int8_t pct;
  int8_t lvl;
} tk_mt_day;

typedef struct {
  bool has_avg;
  double avg_peak_pct;
  int max_weeks;
  int max_weeks_streak;
  int max_days;
  bool has_plan;
  char plan_label[12];
  bool week_maxed[TK_MT_WEEKS];
  tk_mt_day days[TK_MT_DAYS];
} tk_mt_provider;

typedef struct {
  int coding_streak_days; /* -1 = unknown */
  bool stale;
  tk_mt_provider claude, codex;
} tk_max_tracker;

#endif
