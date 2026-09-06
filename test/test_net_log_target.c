/*
 * Reläets adress är åtkomstnyckeln, inte bara en adress: sökvägen
 * `/u/<hemlighet>` räcker för att både läsa panelens siffror och skriva
 * över dem (docs/relay.md). Den här sviten håller den ur loggarna, och
 * håller kvar det som faktiskt felsöker: schema, värd och vilken väg som
 * provades.
 */

#include <stdio.h>
#include <string.h>

#include "../components/torget_net/net_log_target.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static void expect(const char *what, const char *url, bool relay,
                   const char *want) {
  char out[TG_NET_LOG_TARGET_CAP];
  tg_net_log_target(out, sizeof out, url, relay);
  if (strcmp(out, want) != 0) {
    printf("FAIL %s\n  fick:  \"%s\"\n  ville: \"%s\"\n", what, out, want);
    failures++;
  }
}

/* Den riktiga formen: en Worker-värd och en hemlig brevlådesökväg, med
 * endpointen påhängd av TK_*_RELAY_URL. */
#define SECRET "s3kr3t-mailbox-2f9c41a8"
#define RELAY_ORIGIN "https://vibepulse.example.workers.dev"
#define RELAY_URL RELAY_ORIGIN "/u/" SECRET "/api/tokens"

static void test_the_relay_secret_never_reaches_a_log(void) {
  expect("the relay keeps its scheme and host, and loses its path",
         RELAY_URL, true, RELAY_ORIGIN " via relä");

  char out[TG_NET_LOG_TARGET_CAP];
  tg_net_log_target(out, sizeof out, RELAY_URL, true);
  check("no fragment of the secret survives", strstr(out, SECRET) == NULL);
  check("not even the mailbox prefix survives", strstr(out, "/u/") == NULL);
  check("and the endpoint path goes with it",
        strstr(out, "/api/tokens") == NULL);
}

static void test_the_line_still_says_where_and_which_way(void) {
  /* Fel hamn, fel värdnamn och ett dött relä ska gå att skilja åt utan
   * sökvägen — det är hela poängen med att logga något alls. */
  expect("the LAN host and port stay readable",
         "http://192.168.1.7:8737/api/tokens", false,
         "http://192.168.1.7:8737 via LAN");
  expect("a wrong hostname still shows up",
         "http://mac-mini.local:8737/api/max-tracker", false,
         "http://mac-mini.local:8737 via LAN");
  /* Samma adress, två vägar: bara vägordet skiljer raderna åt. */
  expect("the same host is labelled by the route it was tried on",
         RELAY_ORIGIN "/u/" SECRET "/api/github", false,
         RELAY_ORIGIN " via LAN");
}

static void test_anything_credential_shaped_is_dropped(void) {
  /* Användarinfo är en referens, inte en adress. */
  expect("userinfo never survives",
         "https://user:pw@relay.example.com/u/" SECRET, true,
         "https://relay.example.com via relä");
  expect("an @ inside the path cannot pull the path back in",
         "https://relay.example.com/u/a@b/api/tokens", true,
         "https://relay.example.com via relä");
  expect("a query string is a path too",
         "https://relay.example.com?token=" SECRET, true,
         "https://relay.example.com via relä");
  expect("and so is a fragment",
         "https://relay.example.com#" SECRET, true,
         "https://relay.example.com via relä");
}

static void test_a_malformed_address_says_nothing_rather_than_too_much(void) {
  expect("a schemeless address is not guessed at",
         "vibepulse.example.workers.dev/u/" SECRET, true,
         "okänd adress via relä");
  expect("an empty host is not a host",
         "https:///u/" SECRET, true, "okänd adress via relä");
  expect("a bare path is never mistaken for an origin",
         "/u/" SECRET, true, "okänd adress via relä");
  expect("nor is an empty string", "", false, "okänd adress via LAN");
  expect("nor is a missing address", NULL, false, "okänd adress via LAN");
}

static void test_a_long_host_loses_the_host_not_the_route(void) {
  char url[512];
  char host[300];
  memset(host, 'a', sizeof host - 1);
  host[sizeof host - 1] = '\0';
  snprintf(url, sizeof url, "https://%s/u/%s/api/tokens", host, SECRET);

  char out[TG_NET_LOG_TARGET_CAP];
  tg_net_log_target(out, sizeof out, url, true);
  check("an oversized host stays inside the buffer",
        strlen(out) < TG_NET_LOG_TARGET_CAP);
  check("a truncated host still says which way was tried",
        strstr(out, " via relä") != NULL);
  check("and truncation never lets the path back in",
        strstr(out, SECRET) == NULL && strstr(out, "/u/") == NULL);
}

static void test_a_short_buffer_neither_overflows_nor_leaks(void) {
  /* Anropsplatsen äger bufferten; en framtida mindre buffert får kosta
   * tecken, aldrig minne bredvid och aldrig hemligheten. */
  for (size_t cap = 1; cap <= 24; cap++) {
    char canary[64];
    memset(canary, '#', sizeof canary);
    tg_net_log_target(canary, cap, RELAY_URL, true);
    check("the write stays inside the given cap",
          canary[cap] == '#' && memchr(canary, '\0', cap) != NULL);
    check("a short buffer never spills the secret",
          strstr(canary, SECRET) == NULL);
  }

  /* cap 0 får inte skriva en enda byte. */
  char none[4] = { '#', '#', '#', '#' };
  tg_net_log_target(none, 0, RELAY_URL, true);
  check("a zero cap writes nothing at all", none[0] == '#');
}

int main(void) {
  test_the_relay_secret_never_reaches_a_log();
  test_the_line_still_says_where_and_which_way();
  test_anything_credential_shaped_is_dropped();
  test_a_malformed_address_says_nothing_rather_than_too_much();
  test_a_long_host_loses_the_host_not_the_route();
  test_a_short_buffer_neither_overflows_nor_leaks();

  if (failures) {
    printf("%d test(er) föll\n", failures);
    return 1;
  }
  printf("OK: reläets hemliga sökväg kan inte nå en logg\n");
  return 0;
}
