#ifndef SOLGLANCE_FMT_SV_H
#define SOLGLANCE_FMT_SV_H

#include <stddef.h>

/*
 * sv-SE number formatting for the shelf display. Pure C11, no dependencies,
 * unit-tested on the host with clang (firmware/test/) so the fiddly parts —
 * U+00A0 grouping, comma decimals, integer-scaled ticker split — are settled
 * long before the hardware exists.
 *
 * All outputs are NUL-terminated UTF-8. Buffers are caller-owned; every
 * function truncates safely rather than overrunning.
 */

/* "9524" -> "9 524" (U+00A0 as thousands separator). Negative values are
 * clamped to 0: nothing on this device can honestly be negative, and a
 * defaulted minus sign would be a lie with a typo in it. */
void sv_group_ll(long long v, char *out, size_t cap);

/* Mirror of the web's splitTicking(v, tailDigits=2) — the SAME tail width as
 * the Översikt hero, so the shelf feels as alive as the site (Niclas beslut
 * 2026-08-06). Integer-scaled so the ticker can NEVER render an öre low.
 * 100.6042 -> whole "100", frac "60", tail "42". whole is grouped. */
typedef struct {
  char whole[24]; /* grouped kronor, e.g. "103 147" */
  char frac[3];   /* two fixed decimals, e.g. "60" */
  char tail[3];   /* two streaming decimals, e.g. "42" */
} sv_ticker_str;

void sv_ticker_split(double v, sv_ticker_str *out);

/* Percent with one comma decimal: 60.7 -> "60,7". 100.049 -> "100,0". */
void sv_pct_1(double pct, char *out, size_t cap);

/* "2026-08-04" -> "4 aug" (svenska månadskorta, gemener — samma format som
 * bänkens fmtSettledDay). Ogiltig indata ger "–": ett streck är ärligare än
 * en gissad kalender. cap >= 7 räcker alltid. */
void sv_day_short(const char *day, char *out, size_t cap);

#endif
