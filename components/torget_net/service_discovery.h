#ifndef TORGET_SERVICE_DISCOVERY_H
#define TORGET_SERVICE_DISCOVERY_H

#include <stdbool.h>
#include <stddef.h>

typedef enum {
  TG_SERVICE_SOURCE_CONFIGURED = 0,
  TG_SERVICE_SOURCE_DISCOVERED = 1,
} tg_service_source;

/* Resolve the local _vibepulse._tcp service and append one fixed endpoint
 * path.  Discovery is best-effort: multicast failure, missing records, or a
 * bounded query timeout returns the configured full URL unchanged.  A NULL
 * configured_url is allowed (a feed switched on in LABS on a panel whose
 * secrets.h never had its URL): discovery still runs, and without a
 * discovered origin the call fails instead of dereferencing the fallback. */
bool torget_service_endpoint_url(const char *path, const char *configured_url,
                                 char *url, size_t cap,
                                 tg_service_source *source);

/* Feed the exact transport outcome back into the cache.  A successful
 * discovered origin becomes the NVS last-known-good; a failed one is backed
 * off and another advertised host may win the next query. */
void torget_service_note_result(tg_service_source source, const char *url,
                                bool ok);

#endif
