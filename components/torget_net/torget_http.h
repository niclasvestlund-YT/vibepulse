#ifndef TORGET_HTTP_H
#define TORGET_HTTP_H

#include <stdbool.h>
#include <stddef.h>

/*
 * Torgets HTTP-klient för glance-mönstrets endpoints: EN begränsad GET som
 * antingen ger hela kroppen eller ingenting. Skördad ur Solelkollens
 * glance_http.c när Tokenmätaren blev andra användaren (biblioteksregeln).
 *
 * Taket (cap) är en spärr mot en oväntad felsida, inte en budget: en kropp
 * som spränger det avvisas hellre än trunkeras, för en trunkerad JSON är
 * exakt det parsrarna ska slippa gissa om.
 *
 * Sant bara vid rent 200-svar inom taket; buf är då NUL-terminerad och
 * *len_out satt. Vid falskt är buf odefinierad — anroparen behåller sina
 * senaste goda värden och låter staleness byggas upp. Aldrig halva data
 * på skärmen.
 *
 * HTTPS via esp_crt_bundle (överlever certrotation); vanlig HTTP fungerar
 * också — Tokenmätarens Mac-tjänst bor på LAN:et utan certifikat.
 */
bool torget_http_get(const char *url, char *buf, size_t cap, size_t *len_out);

#endif
