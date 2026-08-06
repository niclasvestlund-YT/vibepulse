#include "ticker.h"

void sg_ticker_set(sg_ticker *t, int64_t now_us, double base_kr, double per_hour) {
  if (!t) return;
  t->anchor_us = now_us;
  /* The server never reports negative money; a clamp here keeps a glitched
   * payload from ever rendering a minus on the shelf. */
  t->base_kr = base_kr > 0 ? base_kr : 0;
  t->per_hour = per_hour > 0 ? per_hour : 0; /* the sun does not un-shine */
  t->has_data = 1;
}

double sg_ticker_value(const sg_ticker *t, int64_t now_us) {
  if (!t || !t->has_data) return 0.0;
  int64_t elapsed_us = now_us - t->anchor_us;
  if (elapsed_us < 0) elapsed_us = 0; /* monotonic in practice; belt anyway */
  return t->base_kr + t->per_hour * ((double)elapsed_us / 3.6e9);
}
