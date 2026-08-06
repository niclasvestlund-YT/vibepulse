#include "fmt_sv.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

/* U+00A0 in UTF-8. Same separator toLocaleString('sv-SE') emits on the web,
 * and the LVGL fonts include the glyph (see spec/ui-spec.md font table). */
#define NBSP "\xC2\xA0"

void sv_group_ll(long long v, char *out, size_t cap) {
  if (cap == 0) return;
  if (v < 0) v = 0; /* reasoned clamp — see header */

  char digits[24];
  int n = snprintf(digits, sizeof digits, "%lld", v);
  if (n < 0) { out[0] = '\0'; return; }

  size_t pos = 0;
  for (int i = 0; i < n; i++) {
    /* A separator goes before this digit when the remaining count is a
     * multiple of three (but never before the first digit). */
    int remaining = n - i;
    if (i > 0 && remaining % 3 == 0) {
      if (pos + 2 >= cap) break;
      memcpy(out + pos, NBSP, 2);
      pos += 2;
    }
    if (pos + 1 >= cap) break;
    out[pos++] = digits[i];
  }
  out[pos < cap ? pos : cap - 1] = '\0';
}

void sv_ticker_split(double v, sv_ticker_str *out) {
  if (!out) return;
  if (!(v > 0)) v = 0; /* clamps negatives AND NaN in one honest move */

  /* Integer-scaled exactly like the web's splitTicking: round once at
   * 1/10000 kr and derive every part from that integer, so 84.27 can never
   * render as "84,26|99". */
  long long scaled = llround(v * 10000.0);
  long long whole = scaled / 10000;
  /* Unsigned, och modulo en gång till: v är redan klampad >= 0, men GCC:s
   * -Wformat-truncation ser inte det genom en long long och fäller bygget.
   * Casten säger 0-9999 rakt ut, så 0-99 två gånger ryms bevisligen. */
  unsigned sub = (unsigned)(scaled % 10000) % 10000u;

  sv_group_ll(whole, out->whole, sizeof out->whole);
  snprintf(out->frac, sizeof out->frac, "%02u", sub / 100u);
  snprintf(out->tail, sizeof out->tail, "%02u", sub % 100u);
}

void sv_day_short(const char *day, char *out, size_t cap) {
  static const char *MON[12] = { "jan", "feb", "mar", "apr", "maj", "jun",
                                 "jul", "aug", "sep", "okt", "nov", "dec" };
  if (cap == 0) return;
  int m = 0, d = 0;
  if (day && strlen(day) == 10) {
    m = (day[5] - '0') * 10 + (day[6] - '0');
    d = (day[8] - '0') * 10 + (day[9] - '0');
  }
  if (m < 1 || m > 12 || d < 1 || d > 31) {
    snprintf(out, cap, "\xE2\x80\x93"); /* en-dash */
    return;
  }
  snprintf(out, cap, "%d %s", d, MON[m - 1]);
}

void sv_pct_1(double pct, char *out, size_t cap) {
  if (cap == 0) return;
  if (!(pct > 0)) pct = 0;

  long long tenths = llround(pct * 10.0);
  char grouped[24];
  sv_group_ll(tenths / 10, grouped, sizeof grouped);
  snprintf(out, cap, "%s,%lld", grouped, tenths % 10);
}
