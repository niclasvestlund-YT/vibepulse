#ifndef TOKENS_H
#define TOKENS_H

#include <stdint.h>

#define TK_QUOTA_LABEL_CAP 17

/*
 * Speglar Mac-tjänstens /api/tokens (kontrakt v2) minus transportfälten —
 * VibePulse-datats kontrakt enligt glance-mönstret: platt JSON, tal inte
 * strängar, en takt så appen kan ticka lokalt. Tjänsten (tools/tokenserver/)
 * kombinerar tre källor:
 *
 *  1. Claude Codes sessionsloggar — tokenvolymen (dag/månad/takt/sessioner).
 *  2. Rate-limit-headrarna från ett minimalt Claude-API-anrop (Clawdmeter-
 *     mönstret) — sessionens 5h-fönster + veckofönstret i procent.
 *  3. Codex CLI:s rollout-loggar (passiv läsning) — Codex fönster.
 *
 * Varje limit är (procent använt, minuter till nollning). Fälten kan vara
 * null i payloaden (nyckelring/probe/loggar otillgängliga, eller planen
 * saknar fönstret) — då är has_* 0 och vyn visar streck, aldrig hittade
 * procent. Tokens är alla som passerat modellen: in + ut + cache.
 */

typedef struct {
  double pct;    /* utnyttjande, 0-100 */
  double delta_pct; /* förändring i samma resetcykel */
  int reset_min; /* minuter till fönstret nollas */
  int has_pct, has_reset, has_delta;
  int stale;     /* 1 när värdet är unexpired last-known-good */
} tk_limit;

typedef enum {
  TK_FORECAST_UNAVAILABLE,
  TK_FORECAST_COLLECTING,
  TK_FORECAST_AT_RESET,
  TK_FORECAST_EXHAUSTS,
} tk_forecast_state;

typedef struct {
  tk_forecast_state state;
  int pct_at_reset;
  double pace_factor;
  int64_t at_epoch;
  int offset_min;
  int has_pct_at_reset;
  int has_pace_factor;
  int has_at_epoch;
  int has_offset_min;
} tk_forecast;

typedef struct {
  /* volymen (alltid närvarande) */
  double day_tokens;          /* idag, lokal Mac-tid */
  double day_tokens_per_hour; /* brinntakten över senaste timmen; 0 = paus */
  int day_sessions;           /* sessioner med aktivitet idag */
  double month_tokens;        /* kalendermånaden */

  /* taken (null-bara). claude_model_week är veckofönstret för tyngsta
   * modellen (Fable/Opus) — tredje raden i Claudes egen usage-panel. */
  tk_limit claude_session, claude_week, claude_model_week;
  tk_limit codex_session, codex_week;
  char claude_model_week_label[TK_QUOTA_LABEL_CAP];
  int has_claude_model_week_label;
  tk_forecast claude_forecast, codex_forecast;
} tk_tokens;

#endif
