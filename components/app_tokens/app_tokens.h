#ifndef APP_TOKENS_H
#define APP_TOKENS_H

#include "torget_app.h"

#include "tokens.h"
#include "agent_status.h"

/*
 * VibePulse: Claude/Codex-usage och agentstatus på hyllan.
 * Datat kommer från den lilla Mac-tjänsten i tools/tokenserver/ (platt JSON
 * enligt glance-mönstret, över LAN); appen tickar lokalt mellan hämtningarna
 * med samma tickerkomponent som Solelkollens kronräknare.
 */

extern const torget_app_t tokens_app;

enum {
  VIEW_CLAUDE_FABLE = 0,
  VIEW_CLAUDE_ALL = 1,
  VIEW_CODEX_WEEKLY = 2,
  VIEW_BURN_RATE = 3,
};

/* Ett lyckat /api/tokens-svar. Snappar tickern, stämplar färskhet och
 * håller skärmen vaken när tokens brinner. Kallas under torget_ui_lock(). */
void tokens_apply(const tk_tokens *t);

/* Ett lyckat /api/agent-status-svar, redan parsat och under UI-låset. */
void tokens_apply_agent_status(const tk_agent_snapshot *snapshot);

/* Targetets 1 Hz-hämtning. Utan TK_AGENT_STATUS_URL loggas avstängt läge
 * och ingen task eller HTTP-klient skapas. */
void tokens_agent_net_start(void);

/* Hoppa till en VibePulse-vy utan animation — bänkens och BMP-dumparnas
 * ratt. */
void tokens_show_view(int idx);

#endif
