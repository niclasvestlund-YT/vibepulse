#ifndef MAX_TRACKER_PARSE_H
#define MAX_TRACKER_PARSE_H

#include <stdbool.h>
#include <stddef.h>

#include "max_tracker.h"

/*
 * Kontraktsvakt för /api/max-tracker, samma regler som tokens- och
 * glance-parsern: fel version, fel veckoantal, {"error": ...}-formen,
 * trasig JSON, saknade eller icke-numeriska fält, tal utanför sitt
 * intervall och fel array-längder avvisas UTAN att röra *out. Anroparen
 * behåller sina senaste goda värden — en halvparsead historik är värre än
 * en gammal.
 *
 * planLabel är undantaget: en syntaktiskt ogiltig etikett (fel typ, tom,
 * för lång, tecken utanför A-Z/0-9/mellanslag) tappas för sig (has_plan =
 * false) medan resten av dokumentet ändå parsas — samma
 * visningssäkerhetsregel som claudeModelWeekLabel i tokens-kontraktet.
 *
 * Hosttestad (test/run.sh) mot de riktiga fixturerna plus fientlig indata —
 * samma kod på Macen som på kortet.
 */
bool tk_max_tracker_parse(const char *json, size_t len, tk_max_tracker *out);

#endif
