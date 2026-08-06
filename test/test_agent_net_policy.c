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

  if (failures == 0) {
    printf("OK: alla agentnät-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
