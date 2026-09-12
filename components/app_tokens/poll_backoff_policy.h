#ifndef POLL_BACKOFF_POLICY_H
#define POLL_BACKOFF_POLICY_H

/*
 * Consecutive-failure backoff for the device pollers (OBS-13). Pure
 * arithmetic, no ESP-IDF, host-tested by test/test_poll_backoff_policy.c.
 *
 * Every poller used to run at a fixed cadence no matter what: a dead
 * tokenserver got 86 400 connect attempts a day from the agent-status
 * poller alone. The server side learned this the hard way (the 429 night,
 * docs/lessons.md); this is the same medicine for the panel.
 *
 * Shape: the first failure keeps the normal cadence (one missed poll is
 * normal -- a laptop lid, a service restart), every further consecutive
 * failure doubles the delay up to the cap, and one success resets it.
 * The note function says when the delay CHANGED so the caller logs
 * transitions only, never every miss.
 */

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  uint32_t base_ms;
  uint32_t cap_ms;
  uint32_t streak;   /* consecutive failures so far */
} tk_poll_backoff;

void tk_poll_backoff_init(tk_poll_backoff *b, uint32_t base_ms,
                          uint32_t cap_ms);

/* The delay to sleep before the next poll, given the current streak. */
uint32_t tk_poll_backoff_delay_ms(const tk_poll_backoff *b);

/* Record one poll outcome. Returns true when the delay for the NEXT poll
 * differs from the delay that was in force before this outcome -- the
 * moment worth one log line (slowing down, or recovered). */
bool tk_poll_backoff_note(tk_poll_backoff *b, bool success);

#endif
