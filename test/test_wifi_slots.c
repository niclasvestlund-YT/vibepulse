#include <stdio.h>
#include <string.h>

#include "../components/torget_wifi/wifi_slots.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static void put(tg_wifi_slot *slot, const char *ssid, const char *pass,
                uint32_t seq) {
  memset(slot, 0, sizeof *slot);
  snprintf(slot->ssid, sizeof slot->ssid, "%s", ssid);
  snprintf(slot->pass, sizeof slot->pass, "%s", pass);
  slot->seq = seq;
}

static void test_validation(void) {
  check("empty ssid rejected", !tg_wifi_ssid_valid(""));
  check("NULL ssid rejected", !tg_wifi_ssid_valid(NULL));
  check("normal ssid accepted", tg_wifi_ssid_valid("Hotell Gast"));
  char too_long[TG_WIFI_SSID_CAP + 4];
  memset(too_long, 'a', sizeof too_long - 1);
  too_long[sizeof too_long - 1] = '\0';
  check("33-byte ssid rejected", !tg_wifi_ssid_valid(too_long));
  /* Ett SSID med radbrytning i hamnar i loggen och i AP-listans HTML —
   * upstream är fientlig (läxan 2026-08-13, NUL-trunkeringen). */
  check("ssid with newline rejected", !tg_wifi_ssid_valid("hem\nAP"));

  check("empty password is a valid open network", tg_wifi_pass_valid(""));
  check("7-char psk rejected", !tg_wifi_pass_valid("1234567"));
  check("8-char psk accepted", tg_wifi_pass_valid("12345678"));
  check("64-char psk rejected", !tg_wifi_pass_valid(
      "0123456789012345678901234567890123456789012345678901234567890123"));
  check("psk with control char rejected", tg_wifi_pass_valid("abc\tdefg") == 0);
}

static void test_write_slot_updates_before_it_evicts(void) {
  tg_wifi_slot slots[TG_WIFI_SLOTS];
  memset(slots, 0, sizeof slots);
  put(&slots[0], "hem", "hemligt1", 5);
  put(&slots[1], "kontor", "kontoret", 9);

  check("known ssid updates its own slot",
        tg_wifi_slot_for_write(slots, TG_WIFI_SLOTS, "hem") == 0);
  check("new ssid takes the first empty slot",
        tg_wifi_slot_for_write(slots, TG_WIFI_SLOTS, "hotell") == 2);

  /* Fullt: den med lägst seq vräks, inte den första bästa. */
  for (int i = 0; i < TG_WIFI_SLOTS; i++) {
    char name[16];
    snprintf(name, sizeof name, "plats%d", i);
    put(&slots[i], name, "losenord", (uint32_t)(10 + i));
  }
  put(&slots[3], "aldrig-igen", "losenord", 2); /* lägst seq */
  check("a full list evicts the least recently working network",
        tg_wifi_slot_for_write(slots, TG_WIFI_SLOTS, "ny-plats") == 3);
  check("a full list still prefers updating a known network",
        tg_wifi_slot_for_write(slots, TG_WIFI_SLOTS, "plats5") == 5);
}

static void test_seq_next_saturates(void) {
  tg_wifi_slot slots[2];
  memset(slots, 0, sizeof slots);
  check("empty list starts at 1", tg_wifi_seq_next(slots, 2) == 1);
  put(&slots[0], "a", "losenord", 7);
  check("next is one past the highest", tg_wifi_seq_next(slots, 2) == 8);
  put(&slots[1], "b", "losenord", UINT32_MAX);
  check("saturates instead of wrapping to zero",
        tg_wifi_seq_next(slots, 2) == UINT32_MAX);
}

static void test_candidate_order(void) {
  tg_wifi_slot remembered[TG_WIFI_SLOTS];
  memset(remembered, 0, sizeof remembered);
  put(&remembered[0], "hotell", "hotellet", 3);
  put(&remembered[1], "hem", "hemligt1", 9);   /* senast lyckad */
  put(&remembered[2], "kontor", "kontoret", 6);

  tg_wifi_slot fixed[2];
  memset(fixed, 0, sizeof fixed);
  put(&fixed[0], "hem", "hemligt1", 0);        /* samma som NVS-posten */
  put(&fixed[1], "iPhone", "hotspot1", 0);

  const tg_wifi_slot *out[TG_WIFI_SLOTS + 2];
  int n = tg_wifi_candidates(remembered, TG_WIFI_SLOTS, fixed, 2, out,
                             TG_WIFI_SLOTS + 2);
  check("every non-empty network appears once", n == 4);
  check("most recently working network is tried first",
        strcmp(out[0]->ssid, "hem") == 0);
  check("then the next most recent", strcmp(out[1]->ssid, "kontor") == 0);
  check("then the oldest remembered", strcmp(out[2]->ssid, "hotell") == 0);
  /* Botten ur secrets.h ligger sist och dubbleras inte: "hem" fanns redan
   * i NVS, så bara hotspoten är kvar att lägga till. */
  check("the compiled-in floor comes last",
        strcmp(out[3]->ssid, "iPhone") == 0);
}

static void test_candidates_survive_an_empty_store(void) {
  tg_wifi_slot remembered[TG_WIFI_SLOTS];
  memset(remembered, 0, sizeof remembered);
  tg_wifi_slot fixed[2];
  memset(fixed, 0, sizeof fixed);
  put(&fixed[0], "hem", "hemligt1", 0);
  /* fixed[1] lämnas tom — en secrets.h utan reservnät. */

  const tg_wifi_slot *out[TG_WIFI_SLOTS + 2];
  int n = tg_wifi_candidates(remembered, TG_WIFI_SLOTS, fixed, 2, out,
                             TG_WIFI_SLOTS + 2);
  /* Det här ÄR en färsk panel: tom NVS får aldrig betyda noll kandidater,
   * för då slutar hemnätet fungera efter en uppdatering. */
  check("a fresh panel still has its compiled-in home network", n == 1);
  check("and it is the home network", strcmp(out[0]->ssid, "hem") == 0);

  int none = tg_wifi_candidates(remembered, TG_WIFI_SLOTS, NULL, 0, out,
                                TG_WIFI_SLOTS + 2);
  check("no networks at all is zero, not a crash", none == 0);
}

static void test_switching(void) {
  check("three misses stay on the same network", !tg_wifi_should_switch(3));
  check("four misses switch", tg_wifi_should_switch(4));
}

static void test_setup_window_opens_only_when_it_can_help(void) {
  const int64_t s = 1000000LL;

  check("a panel with an IP never opens the setup window",
        !tg_wifi_setup_should_open(true, 500 * s, 0, 0));
  check("a short outage does not open it",
        !tg_wifi_setup_should_open(false, 60 * s, 30 * s, 0));
  check("ninety seconds without an IP opens it",
        tg_wifi_setup_should_open(false, 200 * s, 100 * s, 0));
  /* Kylperioden: en stängd panel som fortfarande saknar nät får inte
   * öppna ett nytt fönster i samma andetag. */
  check("a window that just closed holds the next one back",
        !tg_wifi_setup_should_open(false, 300 * s, 100 * s, 290 * s));
  check("after the cooldown it may open again",
        tg_wifi_setup_should_open(false, 500 * s, 100 * s, 290 * s));
  /* Har vi aldrig varit utan IP finns ingen startpunkt att mäta från. */
  check("an unknown outage start never opens the window",
        !tg_wifi_setup_should_open(false, 900 * s, 0, 0));
}

static void test_search_screen_lets_the_boot_screen_finish(void) {
  const int64_t s = 1000000LL;

  /* Bootskärmen äger de första 45 sekunderna och ger upp där. Tog nätsidan
   * glaset direkt vid boot skulle två lager slåss om samma pixlar. */
  check("the network screen stays away during boot",
        !tg_wifi_search_ui_visible(false, 10 * s, 1 * s));
  check("and while the boot screen is still up at 45 s",
        !tg_wifi_search_ui_visible(false, 44 * s, 1 * s));
  check("after a minute without an IP it explains itself",
        tg_wifi_search_ui_visible(false, 61 * s, 1 * s));
  /* En router som startar om ska inte svartna hela skärmen. */
  check("a short outage never takes the glass",
        !tg_wifi_search_ui_visible(false, 500 * s, 470 * s));
  check("a panel with an IP never shows it",
        !tg_wifi_search_ui_visible(true, 500 * s, 0));
}

static void test_setup_window_closes(void) {
  const int64_t s = 1000000LL;

  check("a window that was never opened cannot close",
        !tg_wifi_setup_should_close(100 * s, 0, 0));
  check("an open window with no join stays open",
        !tg_wifi_setup_should_close(400 * s, 100 * s, 0));
  check("ten minutes closes it",
        tg_wifi_setup_should_close(701 * s, 100 * s, 0));
  /* Klienten ska hinna läsa svaret innan AP:n rycks undan. */
  check("a fresh join keeps the AP up for the linger",
        !tg_wifi_setup_should_close(302 * s, 100 * s, 300 * s));
  check("after the linger a joined window closes",
        tg_wifi_setup_should_close(306 * s, 100 * s, 300 * s));
}

static void test_setup_phase_owns_key3_without_accidental_release(void) {
  check("idle leaves KEY3 to apps",
        !tg_wifi_setup_owns_input(TG_WIFI_PHASE_IDLE));
  check("starting owns KEY3",
        tg_wifi_setup_owns_input(TG_WIFI_PHASE_STARTING));
  check("open owns KEY3",
        tg_wifi_setup_owns_input(TG_WIFI_PHASE_OPEN));
  check("joining owns KEY3",
        tg_wifi_setup_owns_input(TG_WIFI_PHASE_JOINING));
  check("joined owns KEY3",
        tg_wifi_setup_owns_input(TG_WIFI_PHASE_JOINED));
  check("failed owns KEY3 until dismissed",
        tg_wifi_setup_owns_input(TG_WIFI_PHASE_FAILED));

  check("starting ignores the triggering release",
        !tg_wifi_setup_can_close(TG_WIFI_PHASE_STARTING));
  check("idle cannot close",
        !tg_wifi_setup_can_close(TG_WIFI_PHASE_IDLE));
  check("open is dismissible",
        tg_wifi_setup_can_close(TG_WIFI_PHASE_OPEN));
  check("joining is dismissible",
        tg_wifi_setup_can_close(TG_WIFI_PHASE_JOINING));
  check("joined is dismissible",
        tg_wifi_setup_can_close(TG_WIFI_PHASE_JOINED));
  check("failed is dismissible",
        tg_wifi_setup_can_close(TG_WIFI_PHASE_FAILED));
}

static void test_join_submissions_retry_and_explain_failures(void) {
  check("new submission applies once", tg_wifi_join_should_apply(4, 3));
  check("same submission is deduplicated",
        !tg_wifi_join_should_apply(4, 4));
  check("zero is not a submission", !tg_wifi_join_should_apply(0, 4));
  check("later retry applies", tg_wifi_join_should_apply(5, 4));

  check("reason 15 is a password retry",
        tg_wifi_disconnect_status(15) == TG_WIFI_JOIN_RETRY_PASSWORD);
  check("reason 204 is a password retry",
        tg_wifi_disconnect_status(204) == TG_WIFI_JOIN_RETRY_PASSWORD);
  check("network missing explains 2.4 GHz",
        tg_wifi_disconnect_status(201) == TG_WIFI_JOIN_RETRY_NOT_FOUND);
  check("no disconnect is still connecting",
        tg_wifi_disconnect_status(0) == TG_WIFI_JOIN_CONNECTING);
  check("other failures stay retryable",
        tg_wifi_disconnect_status(8) == TG_WIFI_JOIN_RETRY_CONNECTION);
}

static void test_late_ip_recovers_transient_join_failure(void) {
  /* Captured sequence on 1.91: reason 201, then association and DHCP success. */
  tg_wifi_join_status status = tg_wifi_disconnect_status(201);
  check("transient failure without IP cannot save credentials",
        !tg_wifi_join_should_accept(status, true, false, false));
  check("late IP overrides the stale not-found error",
        tg_wifi_join_should_accept(status, true, false, true));
  check("already accepted does not write NVS again",
        !tg_wifi_join_should_accept(TG_WIFI_JOIN_CONNECTED, true, false, true));
  check("old IP before applying this submission is not proof",
        !tg_wifi_join_should_accept(status, true, true, true));
  check("failed-start or abandoned trial cannot save fallback credentials",
        !tg_wifi_join_should_accept(status, false, false, true));
  check("an idle form cannot save credentials",
        !tg_wifi_join_should_accept(TG_WIFI_JOIN_IDLE, true, false, true));
  check("late success also clears a previous handshake failure",
        tg_wifi_join_should_accept(TG_WIFI_JOIN_RETRY_PASSWORD, true, false, true));
}

static void test_dma_gates_protect_the_flush(void) {
  const size_t flush = 12 * 480 * 2; /* 11 520 — panelflushens block */
  const size_t open_floor = TG_WIFI_SETUP_DMA_OPEN_FACTOR * flush +
                            TG_WIFI_SETUP_DMA_OPEN_RESERVE_BYTES;

  check("plenty of DMA opens", tg_wifi_setup_dma_ok_to_open(200000, flush));
  check("exactly the open floor opens",
        tg_wifi_setup_dma_ok_to_open(open_floor, flush));
  check("below the open floor refuses",
        !tg_wifi_setup_dma_ok_to_open(open_floor - 1, flush));

  /* Kalibreringen mot verkligheten: v0.5.0:s friska baslinje på
   * torget-home-01 var 40960 byte (serielogg 2026-08-18). En grind som
   * vägrar en frisk panel dödar funktionen den skyddar — faktorn valdes
   * så att baslinjen passerar. Faller det här testet har någon höjt
   * faktorn över uppmätt verklighet. */
  check("the measured healthy baseline passes the open gate",
        tg_wifi_setup_dma_ok_to_open(40960, flush));

  /* v0.7.0 med 256 KiB LVGL-pool, Wi-Fi-signal och reläklient har en lägre
   * men stabil frisk baslinje: 31744 byte på riktig torget-home-01
   * (2026-08-21). Grinden får inte återigen göra telefonsetup omöjlig. */
  check("the current healthy panel baseline passes the open gate",
        tg_wifi_setup_dma_ok_to_open(31744, flush));

  /* Fortsättningsgrinden efter APSTA-bytet: under x2 rivs fönstret hellre
   * än att httpd + DNS staplas ovanpå ett block som redan är i farozonen. */
  check("above 2x continues",
        tg_wifi_setup_dma_ok_to_continue(2 * flush, flush));
  check("below 2x aborts",
        !tg_wifi_setup_dma_ok_to_continue(2 * flush - 1, flush));

  /* Ett okonfigurerat flushvärde får aldrig göra grinden till en
   * papperstiger — "vet inte" betyder nej. */
  check("an unset flush size refuses to open",
        !tg_wifi_setup_dma_ok_to_open(200000, 0));
  check("an unset flush size refuses to continue",
        !tg_wifi_setup_dma_ok_to_continue(200000, 0));
  check("an impossible flush cannot wrap the open floor",
        !tg_wifi_setup_dma_ok_to_open(SIZE_MAX, SIZE_MAX / 2 + 1));
}

int main(void) {
  test_late_ip_recovers_transient_join_failure();
  test_dma_gates_protect_the_flush();
  test_validation();
  test_write_slot_updates_before_it_evicts();
  test_seq_next_saturates();
  test_candidate_order();
  test_candidates_survive_an_empty_store();
  test_switching();
  test_setup_window_opens_only_when_it_can_help();
  test_search_screen_lets_the_boot_screen_finish();
  test_setup_window_closes();
  test_setup_phase_owns_key3_without_accidental_release();
  test_join_submissions_retry_and_explain_failures();

  if (failures == 0) {
    printf("OK: all WiFi slot/window policy tests pass\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
