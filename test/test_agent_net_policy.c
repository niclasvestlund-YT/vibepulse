#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "../components/app_tokens/agent_net_policy.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

typedef struct {
  tk_agent_http_response *response;
  size_t remaining;
  int open_calls;
  int fetch_calls;
  int read_calls;
  int close_calls;
} fake_http;

static int fake_open(void *context) {
  fake_http *fake = context;
  fake->open_calls++;
  return 0;
}

static int64_t fake_fetch_headers(void *context) {
  fake_http *fake = context;
  fake->fetch_calls++;
  return 0; /* chunked/okänd längd */
}

static int fake_status(void *context) {
  (void)context;
  return 200;
}

static int fake_read(void *context, char *scratch, int capacity) {
  fake_http *fake = context;
  fake->read_calls++;
  size_t amount = fake->remaining;
  if (amount > (size_t)capacity) amount = (size_t)capacity;
  memset(scratch, 'x', amount);
  (void)tk_agent_http_response_append(fake->response, scratch, amount);
  fake->remaining -= amount;
  return (int)amount;
}

static bool fake_complete(void *context) {
  return ((fake_http *)context)->remaining == 0;
}

static void fake_close(void *context) {
  ((fake_http *)context)->close_calls++;
}

static const tk_agent_http_io fake_io = {
  .open = fake_open,
  .fetch_headers = fake_fetch_headers,
  .get_status = fake_status,
  .read = fake_read,
  .is_complete = fake_complete,
  .close = fake_close,
};

int main(void) {
  tk_agent_http_response response;
  char exact[TK_AGENT_HTTP_BODY_CAP - 1];
  memset(exact, 'x', sizeof exact);

  tk_agent_http_response_reset(&response);
  check("reset nollställer längd", response.len == 0);
  check("reset nollställer overflow", !response.overflow);
  check("reset nollställer status", response.status == 0);
  check("reset strängterminerar tom kropp", response.body[0] == '\0');

  check("cap minus terminator ryms",
        tk_agent_http_response_append(&response, exact, sizeof exact));
  check("exakt gräns ger full längd", response.len == sizeof exact);
  check("exakt gräns termineras inom buffer",
        response.body[TK_AGENT_HTTP_BODY_CAP - 1] == '\0');

  tk_agent_http_response_reset(&response);
  check("hel buffer avvisas",
        !tk_agent_http_response_append(&response, exact, sizeof exact) ||
        !tk_agent_http_response_append(&response, "y", 1));
  check("overflow markeras", response.overflow);
  check("overflow skriver inte utanför sista giltiga terminatorn",
        response.body[response.len] == '\0');

  tk_agent_http_response_reset(&response);
  check("första chunk ryms",
        tk_agent_http_response_append(&response, "abc", 3));
  check("overflowande chunk avvisas utan delkopiering",
        !tk_agent_http_response_append(&response, exact, sizeof exact));
  check("tidigare data och längd bevaras vid overflow",
        response.len == 3 && memcmp(response.body, "abc", 3) == 0 &&
            response.body[3] == '\0');

  tk_agent_http_response_reset(&response);
  response.status = 200;
  check("endast komplett lyckad 200 får appliceras",
        tk_agent_http_response_can_apply(&response, true, true));
  check("transportfel gateas bort",
        !tk_agent_http_response_can_apply(&response, false, true));
  check("parserfel gateas bort",
        !tk_agent_http_response_can_apply(&response, true, false));
  response.status = 204;
  check("annan HTTP-status gateas bort",
        !tk_agent_http_response_can_apply(&response, true, true));
  response.status = 200;
  response.overflow = true;
  check("overflow gateas bort",
        !tk_agent_http_response_can_apply(&response, true, true));

  fake_http bounded = {
    .response = &response,
    .remaining = 2000,
  };
  tk_agent_http_fetch_result fetch =
      tk_agent_http_fetch_bounded(&bounded, &response, &fake_io);
  check("multi-chunk overflow avvisas av adaptern",
        fetch == TK_AGENT_HTTP_FETCH_OVERFLOW && response.overflow);
  check("overflow stänger socketen men inte klienthandtaget",
        bounded.close_calls == 1 && bounded.open_calls == 1);
  check("overflow avbryter innan resten av kroppen läses",
        bounded.remaining > 0 && bounded.read_calls == 3);

  fake_http valid = {
    .response = &response,
    .remaining = 1000,
  };
  fetch = tk_agent_http_fetch_bounded(&valid, &response, &fake_io);
  check("komplett bounded svar accepteras",
        fetch == TK_AGENT_HTTP_FETCH_OK && response.len == 1000);
  check("komplett svar stänger socketen för dokumenterad handle-återanvändning",
        valid.close_calls == 1 && valid.remaining == 0);

  if (failures == 0) {
    printf("OK: alla agentnät-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
