#ifndef AGENT_NET_POLICY_H
#define AGENT_NET_POLICY_H

#include <stdbool.h>
#include <stddef.h>

#define TK_AGENT_HTTP_BODY_CAP 1536

typedef struct {
  char body[TK_AGENT_HTTP_BODY_CAP];
  size_t len;
  bool overflow;
  int status;
} tk_agent_http_response;

void tk_agent_http_response_reset(tk_agent_http_response *response);
bool tk_agent_http_response_append(tk_agent_http_response *response,
                                   const char *data, size_t len);
bool tk_agent_http_response_can_apply(
    const tk_agent_http_response *response, bool transport_ok,
    bool parser_ok);

#endif
