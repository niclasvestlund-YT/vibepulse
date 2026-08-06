#ifndef TOKENS_H
#define TOKENS_H

/*
 * Speglar Mac-tjänstens /api/tokens minus transportfälten — Tokenmätarens
 * datakontrakt enligt glance-mönstret: platt JSON, tal inte strängar, och
 * en takt så appen kan ticka lokalt mellan hämtningarna. Tjänsten bor i
 * tools/tokenserver/ och räknar Claude Code-användningen ur sessionsloggarna
 * på Macen; skärmen hämtar över LAN.
 *
 * Tokens är alla som passerat modellen: in + ut + cacheskrivning +
 * cacheläsning. Det är samma totalsiffra som usage-verktygen visar, och
 * den enda som ärligt beskriver "hur mycket har jag malt idag".
 */
typedef struct {
  double day_tokens;          /* idag, lokal Mac-tid */
  double day_tokens_per_hour; /* brinntakten över senaste timmen; 0 = paus */
  int day_sessions;           /* sessioner med aktivitet idag */
  double month_tokens;        /* kalendermånaden */
} tk_tokens;

#endif
