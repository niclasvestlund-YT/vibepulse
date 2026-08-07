#ifndef AGENT_NET_POLICY_H
#define AGENT_NET_POLICY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TK_AGENT_HTTP_BODY_CAP 4096

typedef struct {
  char body[TK_AGENT_HTTP_BODY_CAP];
  size_t len;
  bool overflow;
  int status;
} tk_agent_http_response;

typedef struct {
  int (*open)(void *context);
  int64_t (*fetch_headers)(void *context);
  int (*get_status)(void *context);
  int (*read)(void *context, char *buffer, int capacity);
  bool (*is_complete)(void *context);
  void (*close)(void *context);
} tk_agent_http_io;

typedef enum {
  TK_AGENT_HTTP_FETCH_OK,
  TK_AGENT_HTTP_FETCH_IO_ERROR,
  TK_AGENT_HTTP_FETCH_OVERFLOW,
} tk_agent_http_fetch_result;

void tk_agent_http_response_reset(tk_agent_http_response *response);
bool tk_agent_http_response_append(tk_agent_http_response *response,
                                   const char *data, size_t len);
bool tk_agent_http_response_can_apply(
    const tk_agent_http_response *response, bool transport_ok,
    bool parser_ok);
tk_agent_http_fetch_result tk_agent_http_fetch_bounded(
    void *context, tk_agent_http_response *response,
    const tk_agent_http_io *io);

#endif
