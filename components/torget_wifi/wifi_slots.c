#include "wifi_slots.h"

#include <string.h>

bool tg_wifi_ssid_valid(const char *ssid) {
  if (!ssid) return false;
  size_t n = strlen(ssid);
  if (n < 1 || n > TG_WIFI_SSID_CAP - 1) return false;
  for (size_t i = 0; i < n; i++) {
    unsigned char c = (unsigned char)ssid[i];
    /* Styrtecken och DEL kan aldrig komma från en riktig nätverksnamn-ruta;
     * de kommer från en trasig klient eller ett försök att smuggla in en
     * radbrytning i något som senare loggas. */
    if (c < 0x20 || c == 0x7F) return false;
  }
  return true;
}

bool tg_wifi_pass_valid(const char *pass) {
  if (!pass) return false;
  size_t n = strlen(pass);
  if (n == 0) return true; /* öppet nät */
  if (n < 8 || n > TG_WIFI_PASS_CAP - 1) return false;
  for (size_t i = 0; i < n; i++) {
    unsigned char c = (unsigned char)pass[i];
    if (c < 0x20 || c == 0x7F) return false;
  }
  return true;
}

int tg_wifi_slot_find(const tg_wifi_slot *slots, int n, const char *ssid) {
  if (!slots || !ssid || !ssid[0]) return -1;
  for (int i = 0; i < n; i++) {
    if (slots[i].seq == 0 || slots[i].ssid[0] == '\0') continue;
    if (strncmp(slots[i].ssid, ssid, TG_WIFI_SSID_CAP) == 0) return i;
  }
  return -1;
}

int tg_wifi_slot_for_write(const tg_wifi_slot *slots, int n, const char *ssid) {
  if (!slots || n <= 0) return -1;

  int existing = tg_wifi_slot_find(slots, n, ssid);
  if (existing >= 0) return existing;

  for (int i = 0; i < n; i++)
    if (slots[i].seq == 0 || slots[i].ssid[0] == '\0') return i;

  /* Fullt: vräk den som varit oanvänd längst. */
  int oldest = 0;
  for (int i = 1; i < n; i++)
    if (slots[i].seq < slots[oldest].seq) oldest = i;
  return oldest;
}

uint32_t tg_wifi_seq_next(const tg_wifi_slot *slots, int n) {
  uint32_t max = 0;
  for (int i = 0; i < n; i++)
    if (slots[i].seq > max) max = slots[i].seq;
  /* Mättnad, inte wrap: en seq som slår runt till 1 skulle göra den
   * nyaste platsen till vräkningskandidat vid nästa nya nät. */
  if (max == UINT32_MAX) return UINT32_MAX;
  return max + 1;
}

static bool already_listed(const tg_wifi_slot *const *out, int n,
                           const char *ssid) {
  for (int i = 0; i < n; i++)
    if (strncmp(out[i]->ssid, ssid, TG_WIFI_SSID_CAP) == 0) return true;
  return false;
}

int tg_wifi_candidates(const tg_wifi_slot *remembered, int n_remembered,
                       const tg_wifi_slot *fixed, int n_fixed,
                       const tg_wifi_slot **out, int out_max) {
  if (!out || out_max <= 0) return 0;
  int n = 0;

  /* Ihågkomna först, högst seq först. Urvalssortering på plats: listan är
   * sex poster lång, så enkelheten är värd mer än ordo. */
  if (remembered) {
    for (;;) {
      int best = -1;
      for (int i = 0; i < n_remembered; i++) {
        const tg_wifi_slot *s = &remembered[i];
        if (s->seq == 0 || s->ssid[0] == '\0') continue;
        if (already_listed(out, n, s->ssid)) continue;
        if (best < 0 || s->seq > remembered[best].seq) best = i;
      }
      if (best < 0 || n >= out_max) break;
      out[n++] = &remembered[best];
    }
  }

  /* Sedan botten ur secrets.h, i sin ordning. */
  if (fixed) {
    for (int i = 0; i < n_fixed && n < out_max; i++) {
      if (fixed[i].ssid[0] == '\0') continue;
      if (already_listed(out, n, fixed[i].ssid)) continue;
      out[n++] = &fixed[i];
    }
  }
  return n;
}

bool tg_wifi_should_switch(int misses) {
  return misses >= TG_WIFI_MISSES_BEFORE_SWITCH;
}

bool tg_wifi_setup_should_open(bool have_ip, int64_t now_us,
                               int64_t no_ip_since_us, int64_t last_close_us) {
  if (have_ip) return false;
  if (no_ip_since_us <= 0) return false;
  if (now_us - no_ip_since_us < TG_WIFI_SETUP_AUTO_US) return false;
  /* Kylperioden gäller bara om ett fönster FAKTISKT stängts (0 = inget
   * har stängts än, och då ska första öppningen inte fördröjas). */
  if (last_close_us > 0 && now_us - last_close_us < TG_WIFI_SETUP_COOLDOWN_US)
    return false;
  return true;
}

bool tg_wifi_setup_owns_input(tg_wifi_setup_phase phase) {
  return phase >= TG_WIFI_PHASE_STARTING && phase <= TG_WIFI_PHASE_FAILED;
}

bool tg_wifi_setup_can_close(tg_wifi_setup_phase phase) {
  return phase == TG_WIFI_PHASE_OPEN || phase == TG_WIFI_PHASE_JOINING ||
         phase == TG_WIFI_PHASE_JOINED || phase == TG_WIFI_PHASE_FAILED;
}

bool tg_wifi_join_should_apply(uint32_t submitted, uint32_t applied) {
  return submitted != 0 && submitted != applied;
}

bool tg_wifi_join_should_accept(tg_wifi_join_status status, bool trial_started,
                               bool applied_now, bool have_ip) {
  return trial_started && !applied_now && have_ip &&
         (status == TG_WIFI_JOIN_CONNECTING ||
          status == TG_WIFI_JOIN_RETRY_PASSWORD ||
          status == TG_WIFI_JOIN_RETRY_NOT_FOUND ||
          status == TG_WIFI_JOIN_RETRY_CONNECTION);
}

tg_wifi_join_status tg_wifi_disconnect_status(int reason) {
  switch (reason) {
    case 0:
      return TG_WIFI_JOIN_CONNECTING;
    case 15:  /* WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT */
    case 204: /* WIFI_REASON_HANDSHAKE_TIMEOUT */
      return TG_WIFI_JOIN_RETRY_PASSWORD;
    case 201: /* WIFI_REASON_NO_AP_FOUND */
      return TG_WIFI_JOIN_RETRY_NOT_FOUND;
    default:
      return TG_WIFI_JOIN_RETRY_CONNECTION;
  }
}

bool tg_wifi_setup_dma_ok_to_open(size_t largest_dma, size_t flush_bytes) {
  /* flush_bytes == 0 vore en felkonfiguration som gjorde grinden till en
   * papperstiger — behandla den som "vet inte" och vägra. */
  if (flush_bytes == 0) return false;
  if (flush_bytes >
      (SIZE_MAX - TG_WIFI_SETUP_DMA_OPEN_RESERVE_BYTES) /
          TG_WIFI_SETUP_DMA_OPEN_FACTOR)
    return false;
  const size_t floor = TG_WIFI_SETUP_DMA_OPEN_FACTOR * flush_bytes +
                       TG_WIFI_SETUP_DMA_OPEN_RESERVE_BYTES;
  return largest_dma >= floor;
}

bool tg_wifi_setup_dma_ok_to_continue(size_t largest_dma, size_t flush_bytes) {
  if (flush_bytes == 0) return false;
  return largest_dma / TG_WIFI_SETUP_DMA_ABORT_FACTOR >= flush_bytes;
}

bool tg_wifi_search_ui_visible(bool have_ip, int64_t now_us,
                               int64_t no_ip_since_us) {
  if (have_ip) return false;
  if (no_ip_since_us <= 0) return false;
  return now_us - no_ip_since_us >= TG_WIFI_SEARCH_UI_US;
}

bool tg_wifi_setup_should_close(int64_t now_us, int64_t opened_us,
                                int64_t joined_ip_us) {
  if (opened_us <= 0) return false;
  if (now_us - opened_us >= TG_WIFI_SETUP_WINDOW_US) return true;
  if (joined_ip_us > 0 && now_us - joined_ip_us >= TG_WIFI_SETUP_LINGER_US)
    return true;
  return false;
}
