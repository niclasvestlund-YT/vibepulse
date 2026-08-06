#include "agent_net_policy.h"

#include <string.h>

void tk_agent_http_response_reset(tk_agent_http_response *response) {
  if (!response) return;
  response->body[0] = '\0';
  response->len = 0;
  response->overflow = false;
  response->status = 0;
}

bool tk_agent_http_response_append(tk_agent_http_response *response,
                                   const char *data, size_t len) {
  if (!response || (!data && len > 0) || response->overflow) return false;
  if (len >= TK_AGENT_HTTP_BODY_CAP - response->len) {
    response->overflow = true;
    return false;
  }
  if (len > 0) memcpy(response->body + response->len, data, len);
  response->len += len;
  response->body[response->len] = '\0';
  return true;
}

bool tk_agent_http_response_can_apply(
    const tk_agent_http_response *response, bool transport_ok,
    bool parser_ok) {
  return response && transport_ok && response->status == 200 &&
         !response->overflow && parser_ok;
}
