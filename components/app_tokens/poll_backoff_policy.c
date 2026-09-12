#include "poll_backoff_policy.h"

void tk_poll_backoff_init(tk_poll_backoff *b, uint32_t base_ms,
                          uint32_t cap_ms) {
  if (!b) return;
  b->base_ms = base_ms == 0 ? 1 : base_ms;
  b->cap_ms = cap_ms < b->base_ms ? b->base_ms : cap_ms;
  b->streak = 0;
}

uint32_t tk_poll_backoff_delay_ms(const tk_poll_backoff *b) {
  if (!b) return 0;
  if (b->streak <= 1) return b->base_ms;
  /* base << (streak - 1), without ever overflowing 32 bits: once the
   * shift alone would pass the cap there is no point computing further. */
  uint32_t delay = b->base_ms;
  for (uint32_t i = 1; i < b->streak; ++i) {
    if (delay >= b->cap_ms / 2) return b->cap_ms;
    delay *= 2;
  }
  return delay < b->cap_ms ? delay : b->cap_ms;
}

bool tk_poll_backoff_note(tk_poll_backoff *b, bool success) {
  if (!b) return false;
  uint32_t before = tk_poll_backoff_delay_ms(b);
  if (success) {
    b->streak = 0;
  } else if (b->streak < UINT32_MAX) {
    b->streak++;
  }
  return tk_poll_backoff_delay_ms(b) != before;
}
