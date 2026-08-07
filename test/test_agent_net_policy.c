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
  const char *cached_bytes;
  size_t cached_len;
  size_t cached_offset;
  size_t network_remaining;
  size_t dropped_on_close;
  size_t bytes_read;
  bool complete_on_fetch;
  bool message_complete;
  int status;
  int64_t content_length;
  int open_calls;
  int fetch_calls;
  int read_calls;
  int close_calls;
} fake_http;

static bool fake_prepare_response(fake_http *fake, const char *cached_bytes,
                                  size_t network_len,
                                  bool complete_on_fetch, int status,
                                  int64_t content_length) {
  if (fake->cached_offset < fake->cached_len) return false;
  fake->cached_bytes = cached_bytes;
  fake->cached_len = cached_bytes ? strlen(cached_bytes) : 0;
  fake->cached_offset = 0;
  fake->network_remaining = network_len;
  fake->dropped_on_close = 0;
  fake->bytes_read = 0;
  fake->complete_on_fetch = complete_on_fetch;
  fake->message_complete = false;
  fake->status = status;
  fake->content_length = content_length;
  fake->read_calls = 0;
  return true;
}

static int fake_open(void *context) {
  fake_http *fake = context;
  fake->open_calls++;
  return 0;
}

static int64_t fake_fetch_headers(void *context) {
  fake_http *fake = context;
  fake->fetch_calls++;
  fake->message_complete = fake->complete_on_fetch;
  return fake->content_length;
}

static int fake_status(void *context) {
  return ((fake_http *)context)->status;
}

static int fake_read(void *context, char *scratch, int capacity) {
  fake_http *fake = context;
  fake->read_calls++;
  size_t amount = 0;
  if (fake->cached_offset < fake->cached_len) {
    amount = fake->cached_len - fake->cached_offset;
  } else {
    amount = fake->network_remaining;
  }
  if (amount > (size_t)capacity) amount = (size_t)capacity;
  if (fake->cached_offset < fake->cached_len) {
    memcpy(scratch, fake->cached_bytes + fake->cached_offset, amount);
    fake->cached_offset += amount;
  } else {
    memset(scratch, 'x', amount);
    fake->network_remaining -= amount;
  }
  fake->bytes_read += amount;
  if (fake->cached_offset == fake->cached_len &&
      fake->network_remaining == 0) {
    fake->message_complete = true;
  }
  return (int)amount;
}

static bool fake_complete(void *context) {
  return ((fake_http *)context)->message_complete;
}

static void fake_close(void *context) {
  fake_http *fake = context;
  fake->close_calls++;
  fake->dropped_on_close += fake->network_remaining;
  fake->network_remaining = 0;
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

  fake_http bounded = {0};
  check("overflow-fixturen förbereds",
        fake_prepare_response(&bounded, NULL,
                              TK_AGENT_HTTP_BODY_CAP + 500,
                              false, 200, 0));
  tk_agent_http_fetch_result fetch =
      tk_agent_http_fetch_bounded(&bounded, &response, &fake_io);
  check("multi-chunk overflow avvisas av adaptern",
        fetch == TK_AGENT_HTTP_FETCH_OVERFLOW && response.overflow);
  check("overflow stänger socketen men inte klienthandtaget",
        bounded.close_calls == 1 && bounded.open_calls == 1);
  check("overflow läser högst cap bytes och lämnar resten oläst",
        bounded.dropped_on_close > 0 &&
            bounded.bytes_read == TK_AGENT_HTTP_BODY_CAP);

  fake_http valid = {0};
  check("valid-fixturen förbereds",
        fake_prepare_response(&valid, NULL, 1000, false, 200, 0));
  fetch = tk_agent_http_fetch_bounded(&valid, &response, &fake_io);
  check("komplett bounded svar accepteras",
        fetch == TK_AGENT_HTTP_FETCH_OK && response.len == 1000);
  check("komplett svar stänger socketen för dokumenterad handle-återanvändning",
        valid.close_calls == 1 && valid.dropped_on_close == 0);

  const char cached_json[] = "{\"seq\":1}";
  fake_http cached = {0};
  check("cached valid-fixturen förbereds",
        fake_prepare_response(&cached, cached_json, 0, true, 200,
                              (int64_t)strlen(cached_json)));
  fetch = tk_agent_http_fetch_bounded(&cached, &response, &fake_io);
  check("komplett body cachad under headers läses ändå",
        fetch == TK_AGENT_HTTP_FETCH_OK &&
            response.len == strlen(cached_json) &&
            memcmp(response.body, cached_json, strlen(cached_json)) == 0);
  check("cached headerpath dräneras till read noll",
        cached.cached_offset == cached.cached_len && cached.read_calls == 2);

  fake_http rejected = {0};
  check("non-200-fixturen förbereds",
        fake_prepare_response(&rejected, "error-prefix", 5000, false, 503,
                              0));
  fetch = tk_agent_http_fetch_bounded(&rejected, &response, &fake_io);
  check("non-200 avvisas utan att body lagras",
        fetch == TK_AGENT_HTTP_FETCH_OK && response.status == 503 &&
            response.len == 0);
  bool rejected_reusable = fake_prepare_response(
      &rejected, cached_json, 0, true, 200, (int64_t)strlen(cached_json));
  check("non-200 cached prefix är dränerat före handle reuse",
        rejected_reusable);
  if (rejected_reusable) {
    fetch = tk_agent_http_fetch_bounded(&rejected, &response, &fake_io);
    check("svar efter non-200 är rent",
          fetch == TK_AGENT_HTTP_FETCH_OK &&
              response.len == strlen(cached_json) &&
              memcmp(response.body, cached_json, strlen(cached_json)) == 0);
  }

  fake_http oversized = {0};
  check("known-oversize-fixturen förbereds",
        fake_prepare_response(&oversized, "oversize-prefix", 5000, false,
                              200, 6000));
  fetch = tk_agent_http_fetch_bounded(&oversized, &response, &fake_io);
  check("known oversize avvisas bounded",
        fetch == TK_AGENT_HTTP_FETCH_OVERFLOW && response.overflow &&
            oversized.bytes_read == TK_AGENT_HTTP_BODY_CAP);
  bool oversized_reusable = fake_prepare_response(
      &oversized, cached_json, 0, true, 200, (int64_t)strlen(cached_json));
  check("known oversize cached prefix är dränerat före handle reuse",
        oversized_reusable);
  if (oversized_reusable) {
    fetch = tk_agent_http_fetch_bounded(&oversized, &response, &fake_io);
    check("svar efter known oversize är rent",
          fetch == TK_AGENT_HTTP_FETCH_OK &&
              response.len == strlen(cached_json) &&
              memcmp(response.body, cached_json, strlen(cached_json)) == 0);
  }

  if (failures == 0) {
    printf("OK: alla agentnät-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
