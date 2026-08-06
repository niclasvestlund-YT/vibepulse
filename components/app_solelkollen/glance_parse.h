#ifndef SOLGLANCE_GLANCE_PARSE_H
#define SOLGLANCE_GLANCE_PARSE_H

#include <stdbool.h>
#include <stddef.h>

#include "glance.h"

/*
 * One parser for both worlds: the simulator reads the sim-fixtures JSON files
 * through it, the ESP target feeds it esp_http_client bodies. Same code path
 * means a fixture that passes on the Mac cannot mis-parse on the shelf.
 *
 * Returns false — and touches nothing — on malformed JSON, an {"error": ...}
 * body (the API's 502 shape), a wrong/missing version, or any missing field.
 * The caller keeps its last good values; honesty over freshness.
 *
 * day_out (cap >= 11) receives "YYYY-MM-DD": the server's civil day, used to
 * spot day-rollover against stale data. Pass NULL to skip.
 */
bool sg_glance_parse(const char *json, size_t len, sg_glance *out,
                     char *day_out, size_t day_cap);

/*
 * /api/glance-sverige (P23). Samma kontrakt som ovan: falskt utan att röra
 * *out vid trasig JSON, error-formen, fel version eller saknade fält.
 * `sharePct` är det enda nullbara fältet — null ger has_share = 0, vyn visar
 * streck. day valideras till exakt 10 tecken precis som glance-dagen.
 */
bool sg_sverige_parse(const char *json, size_t len, sg_sverige *out);

#endif
