#ifndef APP_TOKENS_H
#define APP_TOKENS_H

#include "torget_app.h"

#include "tokens.h"
#include "agent_status.h"
#include "max_tracker.h"

/*
 * VibePulse: Claude/Codex-usage och agentstatus på hyllan.
 * Datat kommer från den lilla Mac-tjänsten i tools/tokenserver/ (platt JSON
 * enligt glance-mönstret, över LAN). Appen har en egen lokal 100 ms-tickare
 * mellan hämtningarna (tick_cb i app.c, se usage_screen_tick) — INTE
 * Solelkollens delade tickerkomponent (ticker.h/sg_ticker); den användningen
 * satt bara i den borttagna volymvyn och försvann härifrån med den.
 */

extern const torget_app_t tokens_app;

enum {
  VIEW_CLAUDE_FABLE = 0,
  VIEW_CLAUDE_ALL = 1,
  VIEW_CODEX_WEEKLY = 2,
  VIEW_BURN_RATE = 3,
  VIEW_TRACKER_CLAUDE = 4,
  VIEW_TRACKER_CODEX = 5,
  VIEW_VALUE = 6,
};

/* Ett lyckat /api/tokens-svar. Snappar tickern, stämplar färskhet och
 * håller skärmen vaken när tokens brinner. Kallas under torget_ui_lock(). */
void tokens_apply(const tk_tokens *t);

/* Ett lyckat /api/agent-status-svar, redan parsat och under UI-låset. */
void tokens_apply_agent_status(const tk_agent_snapshot *snapshot);

/* Ett lyckat /api/max-tracker-svar, redan parsat och under UI-låset. */
void tokens_apply_max_tracker(const tk_max_tracker *t);

/* Targetets 1 Hz-hämtning. Utan TK_AGENT_STATUS_URL loggas avstängt läge
 * och ingen task eller HTTP-klient skapas. */
void tokens_agent_net_start(void);

/* Hoppa till en VibePulse-vy utan animation — bänkens och BMP-dumparnas
 * ratt. */
void tokens_show_view(int idx);

#endif
