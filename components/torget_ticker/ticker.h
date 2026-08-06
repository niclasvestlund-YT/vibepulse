#ifndef SOLGLANCE_TICKER_H
#define SOLGLANCE_TICKER_H

#include <stdint.h>

/*
 * The local ticker: the device polls /api/glance every ~30 s and animates the
 * figure in between from base + rate × elapsed, exactly the anchor dance
 * useLiveTicker.ts does in the browser.
 *
 * One deliberate simplification versus the web: on every successful fetch the
 * ticker SNAPS to the server's base. The server value is authoritative (it
 * already contains everything we extrapolated), and a 30 s extrapolation
 * window keeps any correction below rendering noise. Clock source is the
 * device's own monotonic microseconds (esp_timer_get_time() on target,
 * anything monotonic in tests) — never the server's wall-clock timestamp.
 */

typedef struct {
  int64_t anchor_us; /* monotonic time of the last fetch */
  double base_kr;    /* server's kr at that moment */
  double per_hour;   /* server's krPerHour at that moment */
  int has_data;      /* 0 until the first successful fetch: render "–" */
} sg_ticker;

/* First fetch or refetch: snap the anchor to the authoritative server value. */
void sg_ticker_set(sg_ticker *t, int64_t now_us, double base_kr, double per_hour);

/* Current display value. Before any data: 0.0 (caller renders the dash via
 * has_data, never this zero). Never returns less than base (rate is clamped
 * at >= 0: the sun does not un-shine). */
double sg_ticker_value(const sg_ticker *t, int64_t now_us);

#endif
