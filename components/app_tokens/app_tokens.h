#ifndef APP_TOKENS_H
#define APP_TOKENS_H

#include "torget_app.h"

#include "tokens.h"

/*
 * Tokenmätaren: Claude Code-användningen som en tickande mätare på hyllan.
 * Datat kommer från den lilla Mac-tjänsten i tools/tokenserver/ (platt JSON
 * enligt glance-mönstret, över LAN); appen tickar lokalt mellan hämtningarna
 * med samma tickerkomponent som Solelkollens kronräknare.
 */

extern const torget_app_t tokens_app;

/* Ett lyckat /api/tokens-svar. Snappar tickern, stämplar färskhet och
 * håller skärmen vaken när tokens brinner. Kallas under torget_ui_lock(). */
void tokens_apply(const tk_tokens *t);

#endif
