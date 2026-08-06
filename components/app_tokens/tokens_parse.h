#ifndef TOKENS_PARSE_H
#define TOKENS_PARSE_H

#include <stdbool.h>
#include <stddef.h>

#include "tokens.h"

/*
 * Kontraktsvakt för /api/tokens, samma regler som Solelkollens parser:
 * fel version, {"error": ...}-formen, trasig JSON, saknade eller
 * icke-numeriska fält och negativa tal avvisas UTAN att röra *out.
 * Anroparen behåller sina senaste goda värden och låter staleness byggas
 * upp. En halvparsead mätare är värre än en gammal.
 *
 * Hosttestad (test/run.sh) mot den riktiga fixturen plus fientliga indata —
 * samma kod på Macen som på kortet.
 */
bool tk_tokens_parse(const char *json, size_t len, tk_tokens *out);

#endif
