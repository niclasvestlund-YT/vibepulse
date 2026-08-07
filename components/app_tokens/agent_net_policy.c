#include "agent_net_policy.h"

#include <string.h>

#define TK_AGENT_HTTP_READ_CHUNK 512

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

tk_agent_http_fetch_result tk_agent_http_fetch_bounded(
    void *context, tk_agent_http_response *response,
    const tk_agent_http_io *io) {
  if (!context || !response || !io || !io->open || !io->fetch_headers ||
      !io->get_status || !io->read || !io->is_complete || !io->close) {
    return TK_AGENT_HTTP_FETCH_IO_ERROR;
  }

  tk_agent_http_response_reset(response);
  if (io->open(context) != 0) {
    io->close(context);
    return TK_AGENT_HTTP_FETCH_IO_ERROR;
  }

  int64_t content_length = io->fetch_headers(context);
  if (content_length < 0) {
    io->close(context);
    return TK_AGENT_HTTP_FETCH_IO_ERROR;
  }
  response->status = io->get_status(context);
  bool known_overflow =
      response->overflow || content_length >= TK_AGENT_HTTP_BODY_CAP;
  if (known_overflow) {
    response->overflow = true;
  }
  bool capture = response->status == 200 && !known_overflow;

  char scratch[TK_AGENT_HTTP_READ_CHUNK];
  size_t drained = 0;
  for (;;) {
    size_t budget = TK_AGENT_HTTP_BODY_CAP - drained;
    int capacity = budget < sizeof scratch ? (int)budget
                                            : (int)sizeof scratch;
    int read_len = io->read(context, scratch, capacity);
    if (read_len < 0 || read_len > capacity ||
        (read_len == 0 && !io->is_complete(context))) {
      io->close(context);
      return TK_AGENT_HTTP_FETCH_IO_ERROR;
    }
    if (read_len == 0) break;

    drained += (size_t)read_len;
    if (capture &&
        !tk_agent_http_response_append(response, scratch,
                                       (size_t)read_len)) {
      response->overflow = true;
      io->close(context);
      return TK_AGENT_HTTP_FETCH_OVERFLOW;
    }
    if (drained == TK_AGENT_HTTP_BODY_CAP) break;
  }
  io->close(context);
  return known_overflow ? TK_AGENT_HTTP_FETCH_OVERFLOW
                        : TK_AGENT_HTTP_FETCH_OK;
}
