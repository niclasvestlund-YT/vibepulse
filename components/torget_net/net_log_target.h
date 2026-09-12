#ifndef TORGET_NET_LOG_TARGET_H
#define TORGET_NET_LOG_TARGET_H

/*
 * Loggsäker beskrivning av en hämtnings mål. Ren stränglogik utan ESP-IDF
 * — värdtestad i test/run.sh (test_net_log_target.c).
 *
 * Varför den finns: reläets adress ÄR åtkomstnyckeln. Sökvägen
 * `/u/<hemlighet>` är hela autentiseringen (docs/relay.md), så den som får
 * en seriedump eller en klistrad observabilitetslogg kan både läsa
 * panelens siffror och skriva över dem. En felad hämtning loggade förr
 * hela adressen, och en logg delas långt mer vårdslöst än en hemlighet.
 *
 * Regeln: loggen får se schema + värd och vilken väg som provades, aldrig
 * sökvägen. Redigeringen är ovillkorlig — den frågar inte om just den här
 * adressen råkar vara hemlig — så en ny anropsplats ärver skyddet i
 * stället för att behöva komma ihåg det.
 */

#include <stdbool.h>
#include <stddef.h>

/* Rymmer schema + värd + vägordet. Ett längre värdnamn kapas; en kapad
 * värd är fortfarande bara en värd. */
#define TG_NET_LOG_TARGET_CAP 96

/* Skriver "<schema>://<värd> via LAN" eller "<schema>://<värd> via relä"
 * till out, och NUL-terminerar alltid när cap > 0. En adress utan schema
 * eller värd blir "okänd adress via <väg>": hellre intetsägande än
 * läckande. */
void tg_net_log_target(char *out, size_t cap, const char *url, bool relay);

#endif
