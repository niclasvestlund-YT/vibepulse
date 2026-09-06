#include "net_log_target.h"

#include <stdio.h>
#include <string.h>

/* Plats som vägordet måste få behålla när värden är lång: " via relä" är
 * nio byte i UTF-8, och vilken väg som provades är själva poängen med
 * raden — den får aldrig kapas bort till förmån för fler tecken av ett
 * värdnamn. */
#define TG_NET_LOG_ROUTE_RESERVE 12

/*
 * Ursprunget: schemat och värden, och inget av det som kan bära en
 * hemlighet. Sökväg, fråga och fragment kapas alltid, och användarinfon
 * före '@' hoppas över — den är också en referens, inte en adress.
 */
static bool origin_of(const char *url, char *out, size_t cap) {
  if (url == NULL || out == NULL || cap == 0) return false;

  const char *mark = strstr(url, "://");
  if (mark == NULL || mark == url) return false;
  const size_t scheme_len = (size_t)(mark - url) + 3;

  const char *authority = url + scheme_len;
  const size_t authority_len = strcspn(authority, "/?#");

  size_t host_start = 0;
  for (size_t i = 0; i < authority_len; i++) {
    if (authority[i] == '@') host_start = i + 1;
  }
  const char *host = authority + host_start;
  size_t host_len = authority_len - host_start;
  if (host_len == 0) return false;
  if (scheme_len + 1 >= cap) return false;

  memcpy(out, url, scheme_len);
  const size_t room = cap - scheme_len - 1;
  if (host_len > room) host_len = room;
  memcpy(out + scheme_len, host, host_len);
  out[scheme_len + host_len] = '\0';
  return true;
}

void tg_net_log_target(char *out, size_t cap, const char *url, bool relay) {
  if (out == NULL || cap == 0) return;

  const char *route = relay ? "relä" : "LAN";
  char origin[TG_NET_LOG_TARGET_CAP - TG_NET_LOG_ROUTE_RESERVE];

  if (!origin_of(url, origin, sizeof origin)) {
    snprintf(out, cap, "okänd adress via %s", route);
    return;
  }
  snprintf(out, cap, "%s via %s", origin, route);
}
