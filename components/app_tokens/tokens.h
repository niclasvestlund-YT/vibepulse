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

/*
 * The value multiple: what this month's usage would cost at list API prices,
 * over what the subscription costs. Served under the additive "value" key.
 *
 * state is what the view branches on, because a wrong figure here is worse
 * than no figure. Anything the service could not stand behind arrives as
 * PARTIAL or NO_PLAN_COST rather than as a number, and an absent or
 * unrecognised block is UNAVAILABLE -- dashes, never an invented multiple.
 */
typedef enum {
  TK_VALUE_UNAVAILABLE,  /* key absent, malformed, or state unrecognised */
  TK_VALUE_PARTIAL,      /* too many unpriced tokens; dash everything */
  TK_VALUE_NO_PLAN_COST, /* dollars are real, denominator unknown */
  TK_VALUE_OK,
} tk_value_state;

typedef struct {
  tk_value_state state;
  double value_usd;  /* list-price value of the month's tokens */
  double plan_usd;   /* what the subscription costs per month */
  double multiple;   /* value_usd / plan_usd */
  int has_value_usd, has_plan_usd, has_multiple;
  /* 0 when the plan cost is this build's default rather than a figure the
   * operator stated. The view must not present a default as a precise
   * figure. Rates themselves need no such flag: they are generated from a
   * dated price catalogue, and their age travels as prices_as_of. */
  int cost_configured;
  /* Per-provider breakdown of the same month. Optional: a Claude-only
   * machine reports only the Claude pair, and a payload that omits them
   * entirely still renders the combined figure. Used to attribute the total
   * on screen -- the one honest way to paint a combined page in the reserved
   * provider accents. */
  double claude_usd, codex_usd;
  int has_claude_usd, has_codex_usd;
  /* What each subscription costs, separately. Each one pays for itself or
   * does not on its own, so each earns its own bar and its own break-even
   * point; a blended bar would hide a provider that is not earning its keep. */
  double claude_plan_usd, codex_plan_usd;
  int has_claude_plan_usd, has_codex_plan_usd;
} tk_value;

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
  /* OTA-annonsen: senaste byggets version pa Macen. Enheten jamfor sjalv
   * mot sin korande version — servern pastas aldrig veta vad som kor.
   * 32 = esp_app_desc_t:s versionsfalt; kvotlabelns 17 klippte git
   * describe-strangar tyst (18 tecken, hittat live 2026-08-14). */
  char ota_available_version[32];
  int has_ota_available_version;
  tk_forecast claude_forecast, codex_forecast;
  tk_value value;
} tk_tokens;

#endif
