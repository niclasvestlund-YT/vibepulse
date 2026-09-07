/*
 * Opt-in, outbound-only encrypted Needs You mailbox client.
 *
 * This task owns all relay crypto and HTTPS. It never calls LVGL and takes
 * the UI lock only after a complete response has authenticated, decrypted,
 * passed the stable-view digest, and fit the existing panel parser.
 */
#include "interaction_relay_net.h"

#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"

#include "agent_monitor.h"
#include "agent_status_parse.h"
#include "app_tokens.h"
#include "interaction_relay_crypto.h"
#include "interaction_relay_policy.h"
#include "needs_you_policy.h"
#include "secrets.h"
#include "torget.h"
#include "torget_http.h"

#define TK_IR_POLL_INTERVAL_MS 5000u
#define TK_IR_STATUS_POLL_INTERVAL_MS 10000u
#define TK_IR_HTTP_TIMEOUT_MS 5000u
#define TK_IR_GATE_TIMEOUT_MS 7000u
#define TK_IR_LOOP_MS 100u
#define TK_IR_VERDICT_QUEUE_CAP 4u
#define TK_IR_URL_CAP 320u

static const char *TAG = "interaction-relay";

typedef struct {
  tk_needs_you_verdict verdict;
  tk_ir_decision_context context;
} relay_verdict_item;

typedef struct {
  int status;
  size_t len;
  bool json;
} relay_http_response;

typedef struct {
  unsigned cache_control_count;
  unsigned content_type_count;
  bool json;
  bool invalid;
} relay_response_headers;

typedef struct {
  esp_http_client_handle_t handle;
  relay_response_headers headers;
} relay_http_client;

typedef struct {
  const uint8_t *envelope;
  size_t envelope_len;
  uint64_t expires_at_ms;
  char request_id[TK_IR_REQUEST_ID_CAP];
} relay_next_item;

#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
typedef struct {
  const uint8_t *envelope;
  size_t envelope_len;
  uint64_t expires_at_ms;
  uint64_t stored_at_ms;
} relay_status_item;
#endif

static StaticQueue_t s_queue_control;
static uint8_t s_queue_storage[
    TK_IR_VERDICT_QUEUE_CAP * sizeof(relay_verdict_item)];
static QueueHandle_t s_queue;

/* Fixed buffers keep malformed cloud responses away from the heap and make
 * the maximum TLS/crypto working set visible in the map file. */
static char response[TK_IR_MAX_ENVELOPE_BYTES + 1u];
static uint8_t envelope[TK_IR_MAX_ENVELOPE_BYTES];
static uint8_t crypto_work[TK_IR_WORK_BYTES];
static tk_ir_retry verdict_retry;
static tk_ir_backoff verdict_backoff;
static tk_ir_keys_t relay_keys;
static tk_ir_verdict_binding_t retry_binding;
#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
static tk_ir_status_t decoded_status;
static tk_agent_snapshot decoded_snapshot;
static uint64_t last_status_publication_id;
#endif
static _Atomic uint32_t s_polls_ok;
static _Atomic uint32_t s_requests_applied;
static _Atomic uint32_t s_verdicts_acked;
static _Atomic uint32_t s_status_polls_ok;
static _Atomic uint32_t s_status_applied;
static _Atomic uint32_t s_status_cleared;
static _Atomic uint32_t s_failures;
static _Atomic int s_last_http_status;
static _Atomic bool s_running;
static _Atomic bool s_started;

static uint64_t monotonic_ms(void) {
  int64_t value = esp_timer_get_time();
  return value <= 0 ? 0 : (uint64_t)value / 1000u;
}

static bool random_fill(void *context, uint8_t *out, size_t len) {
  (void)context;
  if (out == NULL || len > UINT_MAX) return false;
  esp_fill_random(out, len);
  return true;
}

static uint32_t jitter_ms(uint32_t ceiling) {
  uint32_t sample = 0;
  esp_fill_random(&sample, sizeof sample);
  return ceiling == 0 ? 0 : sample % ceiling;
}

static bool make_url(char *out, size_t cap, const char *suffix_format,
                     const char *request_id) {
  size_t origin_len = strlen(TK_VIBEPULSE_INTERACTION_RELAY_URL);
  if (origin_len > INT_MAX) return false;
  if (origin_len != 0 &&
      TK_VIBEPULSE_INTERACTION_RELAY_URL[origin_len - 1] == '/') {
    --origin_len;
  }
  int written = request_id == NULL
      ? snprintf(out, cap, "%.*s/v1/mailboxes/%s/requests/next",
                 (int)origin_len, TK_VIBEPULSE_INTERACTION_RELAY_URL,
                 TK_VIBEPULSE_INTERACTION_MAILBOX)
      : snprintf(out, cap, "%.*s/v1/mailboxes/%s/requests/%s/verdict",
                 (int)origin_len, TK_VIBEPULSE_INTERACTION_RELAY_URL,
                 TK_VIBEPULSE_INTERACTION_MAILBOX, request_id);
  (void)suffix_format;
  return written > 0 && (size_t)written < cap;
}

#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
static bool make_status_url(char *out, size_t cap) {
  size_t origin_len = strlen(TK_VIBEPULSE_INTERACTION_RELAY_URL);
  if (origin_len > INT_MAX) return false;
  if (origin_len != 0 &&
      TK_VIBEPULSE_INTERACTION_RELAY_URL[origin_len - 1] == '/') {
    --origin_len;
  }
  int written = snprintf(out, cap, "%.*s/v1/mailboxes/%s/status",
                         (int)origin_len,
                         TK_VIBEPULSE_INTERACTION_RELAY_URL,
                         TK_VIBEPULSE_INTERACTION_MAILBOX);
  return written > 0 && (size_t)written < cap;
}
#endif

static esp_err_t relay_http_event(esp_http_client_event_t *evt) {
  if (evt == NULL || evt->event_id != HTTP_EVENT_ON_HEADER) return ESP_OK;
  relay_response_headers *headers = evt->user_data;
  if (headers == NULL || evt->header_key == NULL ||
      evt->header_value == NULL) {
    if (headers != NULL) headers->invalid = true;
    return ESP_OK;
  }
  if (strcasecmp(evt->header_key, "Cache-Control") == 0) {
    if (headers->cache_control_count < 2u) {
      ++headers->cache_control_count;
    }
    if (headers->cache_control_count > 1u ||
        strcmp(evt->header_value, "no-store") != 0) {
      headers->invalid = true;
    }
  } else if (strcasecmp(evt->header_key, "Content-Type") == 0) {
    if (headers->content_type_count < 2u) {
      ++headers->content_type_count;
    }
    if (headers->content_type_count > 1u ||
        strcmp(evt->header_value, "application/json") != 0) {
      headers->invalid = true;
    } else {
      headers->json = true;
    }
  }
  return ESP_OK;
}

static bool relay_http(relay_http_client *client,
                       esp_http_client_method_t method, const char *url,
                       const uint8_t *body, size_t body_len,
                       relay_http_response *out) {
  if (client == NULL || client->handle == NULL || url == NULL || out == NULL ||
      body_len > TK_IR_MAX_ENVELOPE_BYTES ||
      (body_len != 0 && body == NULL)) {
    return false;
  }
  memset(out, 0, sizeof *out);
  memset(&client->headers, 0, sizeof client->headers);
  bool held = torget_cloud_io_acquire(TK_IR_GATE_TIMEOUT_MS);
  if (!held) return false;
  bool ok = false;
  char authorization[sizeof "Bearer " + 43u];
  int auth_len = snprintf(authorization, sizeof authorization, "Bearer %s",
                          TK_VIBEPULSE_INTERACTION_PANEL_TOKEN);
  if (auth_len <= 0 || (size_t)auth_len >= sizeof authorization) goto done;
  if (esp_http_client_set_url(client->handle, url) != ESP_OK ||
      esp_http_client_set_method(client->handle, method) != ESP_OK ||
      esp_http_client_set_header(client->handle, "Authorization", authorization) !=
          ESP_OK ||
      esp_http_client_set_header(client->handle, "Accept", "application/json") !=
          ESP_OK ||
      esp_http_client_set_header(client->handle, "Cache-Control", "no-store") !=
          ESP_OK) {
    goto done;
  }
  if (body_len != 0 &&
      esp_http_client_set_header(client->handle, "Content-Type", "application/json") !=
          ESP_OK) {
    goto done;
  }
  if (esp_http_client_open(client->handle, (int)body_len) != ESP_OK) goto done;
  if (body_len != 0) {
    size_t sent = 0;
    while (sent < body_len) {
      int wrote = esp_http_client_write(
          client->handle, (const char *)body + sent, (int)(body_len - sent));
      if (wrote <= 0) goto close;
      sent += (size_t)wrote;
    }
  }
  int64_t declared = esp_http_client_fetch_headers(client->handle);
  if (declared > (int64_t)TK_IR_MAX_ENVELOPE_BYTES) goto close;
  out->status = esp_http_client_get_status_code(client->handle);
  atomic_store(&s_last_http_status, out->status);
  relay_response_headers *headers = &client->headers;
  if (headers->invalid || headers->cache_control_count != 1u ||
      headers->content_type_count > 1u) {
    goto close;
  }

  while (out->len <= TK_IR_MAX_ENVELOPE_BYTES) {
    int received = esp_http_client_read(
        client->handle, response + out->len,
        (int)(TK_IR_MAX_ENVELOPE_BYTES + 1u - out->len));
    if (received < 0) goto close;
    if (received == 0) break;
    out->len += (size_t)received;
  }
  if (out->len > TK_IR_MAX_ENVELOPE_BYTES ||
      !esp_http_client_is_complete_data_received(client->handle)) {
    goto close;
  }
  response[out->len] = '\0';
  out->json = headers->content_type_count == 1u && headers->json;
  ok = true;

close:
done:
  /* open() failures used to skip close and leave the reusable handle in an
   * implementation-defined transport state. Always reset it before the next
   * bounded poll; close is explicitly safe when the handle is already INIT. */
  (void)esp_http_client_close(client->handle);
  memset(authorization, 0, sizeof authorization);
  torget_cloud_io_release();
  return ok;
}

static bool decimal_u64(const char *start, const char *end, uint64_t *out) {
  if (start == NULL || end == NULL || out == NULL || start >= end) return false;
  uint64_t value = 0;
  for (const char *cursor = start; cursor < end; ++cursor) {
    if (*cursor < '0' || *cursor > '9') return false;
    uint8_t digit = (uint8_t)(*cursor - '0');
    if (value > (UINT64_MAX - digit) / 10u) return false;
    value = value * 10u + digit;
  }
  *out = value;
  return true;
}

static bool parse_next_wrapper(const char *body, size_t len,
                               relay_next_item *out) {
  static const char prefix[] = "{\"envelope\":";
  static const char middle[] = "},\"expiresAtMs\":";
  static const char request_prefix[] = ",\"requestId\":\"";
  if (body == NULL || out == NULL || len == 0 ||
      strncmp(body, prefix, sizeof prefix - 1) != 0) {
    return false;
  }
  memset(out, 0, sizeof *out);
  const char *envelope_start = body + sizeof prefix - 1;
  if (*envelope_start != '{') return false;
  const char *middle_at = strstr(envelope_start, middle);
  if (middle_at == NULL) return false;
  out->envelope = (const uint8_t *)envelope_start;
  out->envelope_len = (size_t)(middle_at - envelope_start + 1);
  if (out->envelope_len == 0 ||
      out->envelope_len > TK_IR_MAX_ENVELOPE_BYTES) {
    return false;
  }
  const char *expires_start = middle_at + sizeof middle - 1;
  const char *request_at = strstr(expires_start, request_prefix);
  if (request_at == NULL ||
      !decimal_u64(expires_start, request_at, &out->expires_at_ms) ||
      out->expires_at_ms == 0) {
    return false;
  }
  const char *id = request_at + sizeof request_prefix - 1;
  const char *end = body + len;
  if ((size_t)(end - id) != TK_IR_REQUEST_ID_CHARS + 2u ||
      id[TK_IR_REQUEST_ID_CHARS] != '"' ||
      id[TK_IR_REQUEST_ID_CHARS + 1u] != '}') {
    return false;
  }
  memcpy(out->request_id, id, TK_IR_REQUEST_ID_CHARS);
  out->request_id[TK_IR_REQUEST_ID_CHARS] = '\0';
  return true;
}

#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
static bool parse_status_wrapper(const char *body, size_t len,
                                 relay_status_item *out) {
  static const char prefix[] = "{\"envelope\":";
  static const char middle[] = "},\"expiresAtMs\":";
  static const char stored_prefix[] = ",\"storedAtMs\":";
  if (body == NULL || out == NULL || len == 0 ||
      strncmp(body, prefix, sizeof prefix - 1) != 0) {
    return false;
  }
  memset(out, 0, sizeof *out);
  const char *envelope_start = body + sizeof prefix - 1;
  if (*envelope_start != '{') return false;
  const char *middle_at = strstr(envelope_start, middle);
  if (middle_at == NULL) return false;
  out->envelope = (const uint8_t *)envelope_start;
  out->envelope_len = (size_t)(middle_at - envelope_start + 1);
  if (out->envelope_len == 0 ||
      out->envelope_len > TK_IR_MAX_ENVELOPE_BYTES) {
    return false;
  }
  const char *expires_start = middle_at + sizeof middle - 1;
  const char *stored_at = strstr(expires_start, stored_prefix);
  const char *end = body + len;
  if (stored_at == NULL || stored_at >= end || end[-1] != '}' ||
      !decimal_u64(expires_start, stored_at, &out->expires_at_ms) ||
      !decimal_u64(stored_at + sizeof stored_prefix - 1, end - 1,
                   &out->stored_at_ms) ||
      out->expires_at_ms == 0 || out->stored_at_ms == 0 ||
      out->stored_at_ms >= out->expires_at_ms ||
      out->expires_at_ms > 0x1fffffffffffffULL ||
      out->stored_at_ms > 0x1fffffffffffffULL) {
    return false;
  }
  return true;
}
#endif

static void bytes_hex(char out[TK_PENDING_VIEW_SHA256_CAP],
                      const uint8_t bytes[32]) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < 32; ++i) {
    out[i * 2] = digits[bytes[i] >> 4];
    out[i * 2 + 1] = digits[bytes[i] & 15u];
  }
  out[64] = '\0';
}

static bool decode_pending(const relay_next_item *item,
                           tk_pending_interaction *pending) {
  tk_ir_request_t request = {0};
  tk_ir_error_t decoded = tk_ir_decode_request(
      &relay_keys, TK_VIBEPULSE_INTERACTION_MAILBOX, item->request_id,
      item->envelope, item->envelope_len, &request, crypto_work,
      sizeof crypto_work);
  if (decoded != TK_IR_OK) return false;

  time_t wall_seconds = time(NULL);
  if (wall_seconds <= 0) {
    memset(&request, 0, sizeof request);
    return false;
  }
  uint64_t now_wall_ms = (uint64_t)wall_seconds * 1000u;
  uint64_t inner_expiry_ms = (uint64_t)request.expires_at * 1000u;
  uint64_t expiry_ms = inner_expiry_ms < item->expires_at_ms
                           ? inner_expiry_ms : item->expires_at_ms;
  if (expiry_ms <= now_wall_ms) {
    memset(&request, 0, sizeof request);
    return false;
  }
  uint64_t remaining = expiry_ms - now_wall_ms;
  uint32_t remaining_ms = remaining > UINT32_MAX
                              ? UINT32_MAX : (uint32_t)remaining;
  char digest_hex[TK_PENDING_VIEW_SHA256_CAP];
  bytes_hex(digest_hex, request.view_sha256);
  bool parsed = tk_agent_status_parse_relay_view(
      request.view, request.view_len, remaining_ms, digest_hex, pending);
  if (parsed && strcmp(pending->request_id, request.request_id) == 0) {
    pending->source = TK_PENDING_SOURCE_RELAY;
    pending->has_relay_binding = true;
    memcpy(pending->relay_challenge, request.challenge,
           sizeof pending->relay_challenge);
    memcpy(pending->relay_view_sha256, request.view_sha256,
           sizeof pending->relay_view_sha256);
  } else {
    memset(pending, 0, sizeof *pending);
    parsed = false;
  }
  memset(digest_hex, 0, sizeof digest_hex);
  memset(&request, 0, sizeof request);
  return parsed;
}

static void apply_relay(const tk_pending_interaction *pending) {
  torget_ui_lock();
  tk_agent_monitor_apply_relay(pending, esp_timer_get_time());
  torget_ui_unlock();
}

static bool verdict_code(tk_needs_you_verdict verdict,
                         tk_ir_verdict_t *out) {
  if (out == NULL) return false;
  switch (verdict) {
    case TK_NEEDS_YOU_VERDICT_APPROVE:
      *out = TK_IR_VERDICT_APPROVE;
      return true;
    case TK_NEEDS_YOU_VERDICT_DENY:
      *out = TK_IR_VERDICT_DENY;
      return true;
    case TK_NEEDS_YOU_VERDICT_LEAVE_IT:
      *out = TK_IR_VERDICT_TERMINAL;
      return true;
    default:
      return false;
  }
}

bool tk_interaction_relay_queue_verdict(
    tk_needs_you_verdict verdict, const tk_ir_decision_context *context) {
  if (s_queue == NULL || context == NULL || !context->has_relay_binding ||
      context->request_id[0] == '\0' ||
      context->expires_at_ms <= monotonic_ms()) {
    return false;
  }
  relay_verdict_item item = {.verdict = verdict, .context = *context};
  tk_ir_verdict_t ignored;
  if (!verdict_code(verdict, &ignored)) return false;
  return xQueueSend(s_queue, &item, 0) == pdTRUE;
}

static bool begin_verdict_retry(const relay_verdict_item *item) {
  tk_ir_verdict_t verdict;
  if (item == NULL || !verdict_code(item->verdict, &verdict) ||
      !item->context.has_relay_binding ||
      item->context.expires_at_ms <= monotonic_ms()) {
    return false;
  }
  memset(&retry_binding, 0, sizeof retry_binding);
  memcpy(retry_binding.request_id, item->context.request_id,
         sizeof retry_binding.request_id);
  memcpy(retry_binding.challenge, item->context.challenge,
         sizeof retry_binding.challenge);
  memcpy(retry_binding.view_sha256, item->context.relay_view_sha256,
         sizeof retry_binding.view_sha256);
  size_t envelope_len = 0;
  if (tk_ir_encode_verdict_binding(
          &relay_keys, TK_VIBEPULSE_INTERACTION_MAILBOX, &retry_binding,
          verdict, random_fill, NULL, envelope, sizeof envelope,
          &envelope_len, crypto_work, sizeof crypto_work) != TK_IR_OK) {
    memset(&retry_binding, 0, sizeof retry_binding);
    return false;
  }
  bool started = tk_ir_retry_begin(&verdict_retry, envelope, envelope_len,
                                   item->context.expires_at_ms);
  if (started) tk_ir_backoff_init(&verdict_backoff);
  memset(envelope, 0, sizeof envelope);
  return started;
}

static void service_verdict(relay_http_client *send_client) {
  if (!verdict_retry.active) {
    relay_verdict_item item;
    if (xQueueReceive(s_queue, &item, 0) == pdTRUE) {
      if (!begin_verdict_retry(&item)) atomic_fetch_add(&s_failures, 1u);
      memset(&item, 0, sizeof item);
    }
  }
  size_t len = 0;
  uint64_t now_ms = monotonic_ms();
  const uint8_t *body = tk_ir_retry_body(&verdict_retry, now_ms, &len);
  if (body == NULL) {
    if (!verdict_retry.active) memset(&retry_binding, 0, sizeof retry_binding);
    return;
  }
  if (!tk_ir_backoff_ready(&verdict_backoff, now_ms)) return;
  char url[TK_IR_URL_CAP];
  if (!make_url(url, sizeof url,
                "/v1/mailboxes/%s/requests/%s/verdict",
                retry_binding.request_id)) {
    tk_ir_retry_note_failure(&verdict_retry);
    (void)tk_ir_backoff_fail(&verdict_backoff, now_ms,
                             jitter_ms(TK_IR_BACKOFF_MIN_MS));
    atomic_fetch_add(&s_failures, 1u);
    return;
  }
  relay_http_response result;
  bool transport = relay_http(send_client, HTTP_METHOD_POST, url, body, len,
                              &result);
  memset(url, 0, sizeof url);
  if (transport && (result.status == 200 || result.status == 201) &&
      result.len == 0) {
    atomic_fetch_add(&s_verdicts_acked, 1u);
    tk_ir_retry_clear(&verdict_retry);
    tk_ir_backoff_wifi_recovered(&verdict_backoff);
    memset(&retry_binding, 0, sizeof retry_binding);
  } else {
    tk_ir_retry_note_failure(&verdict_retry);
    (void)tk_ir_backoff_fail(&verdict_backoff, now_ms,
                             jitter_ms(TK_IR_BACKOFF_MIN_MS));
    atomic_fetch_add(&s_failures, 1u);
  }
}

static bool poll_request(relay_http_client *poll_client) {
  char url[TK_IR_URL_CAP];
  if (!make_url(url, sizeof url, "/v1/mailboxes/%s/requests/next", NULL)) {
    return false;
  }
  relay_http_response result;
  bool transport = relay_http(poll_client, HTTP_METHOD_GET, url, NULL, 0,
                              &result);
  memset(url, 0, sizeof url);
  if (!transport) return false;
  if (result.status == 204 && result.len == 0 && !result.json) {
    apply_relay(NULL);
    atomic_fetch_add(&s_polls_ok, 1u);
    return true;
  }
  if (result.status != 200 || !result.json || result.len == 0) return false;
  relay_next_item next;
  tk_pending_interaction pending;
  if (!parse_next_wrapper(response, result.len, &next) ||
      !decode_pending(&next, &pending)) {
    return false;
  }
  apply_relay(&pending);
  memset(&pending, 0, sizeof pending);
  atomic_fetch_add(&s_polls_ok, 1u);
  atomic_fetch_add(&s_requests_applied, 1u);
  return true;
}

#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
static bool decode_status_snapshot(const relay_status_item *item,
                                   uint64_t *publication_id) {
  if (item == NULL || publication_id == NULL) return false;
  memset(&decoded_status, 0, sizeof decoded_status);
  memset(&decoded_snapshot, 0, sizeof decoded_snapshot);
  if (tk_ir_decode_status(
          &relay_keys, TK_VIBEPULSE_INTERACTION_MAILBOX,
          item->envelope, item->envelope_len, &decoded_status,
          crypto_work, sizeof crypto_work) != TK_IR_OK) {
    return false;
  }
  time_t wall_seconds = time(NULL);
  if (wall_seconds <= 0) goto reject;
  uint64_t now_wall_ms = (uint64_t)wall_seconds * 1000u;
  uint64_t inner_expiry_ms = (uint64_t)decoded_status.expires_at * 1000u;
  if (item->expires_at_ms <= now_wall_ms ||
      inner_expiry_ms <= now_wall_ms ||
      decoded_status.publication_id <= last_status_publication_id ||
      !tk_agent_status_parse_relay(
          (const char *)decoded_status.status, decoded_status.status_len,
          &decoded_snapshot)) {
    goto reject;
  }
  *publication_id = decoded_status.publication_id;
  memset(&decoded_status, 0, sizeof decoded_status);
  return true;

reject:
  memset(&decoded_status, 0, sizeof decoded_status);
  memset(&decoded_snapshot, 0, sizeof decoded_snapshot);
  return false;
}

static bool poll_status(relay_http_client *client) {
  char url[TK_IR_URL_CAP];
  if (!make_status_url(url, sizeof url)) return false;
  relay_http_response result;
  bool transport = relay_http(client, HTTP_METHOD_GET, url, NULL, 0,
                              &result);
  memset(url, 0, sizeof url);
  if (!transport) return false;
  if (result.status == 204 && result.len == 0 && !result.json) {
    atomic_fetch_add(&s_status_polls_ok, 1u);
    return true;
  }
  if (result.status != 200 || !result.json || result.len == 0) return false;
  relay_status_item item;
  uint64_t publication_id = 0;
  if (!parse_status_wrapper(response, result.len, &item) ||
      !decode_status_snapshot(&item, &publication_id)) {
    return false;
  }
  int64_t now_us = esp_timer_get_time();
  torget_ui_lock();
  bool applied = tokens_apply_agent_status_relay(&decoded_snapshot, now_us);
  torget_ui_unlock();
  memset(&decoded_snapshot, 0, sizeof decoded_snapshot);
  if (applied) {
    last_status_publication_id = publication_id;
    atomic_fetch_add(&s_status_applied, 1u);
  }
  atomic_fetch_add(&s_status_polls_ok, 1u);
  return true;
}

static void clear_stale_status(void) {
  torget_ui_lock();
  bool cleared = tokens_clear_agent_status_relay(esp_timer_get_time());
  torget_ui_unlock();
  if (cleared) atomic_fetch_add(&s_status_cleared, 1u);
}
#endif

static bool create_client(relay_http_client *out) {
  if (out == NULL) return false;
  memset(out, 0, sizeof *out);
  esp_http_client_config_t config = {
      .url = TK_VIBEPULSE_INTERACTION_RELAY_URL,
      .timeout_ms = TK_IR_HTTP_TIMEOUT_MS,
      .crt_bundle_attach = esp_crt_bundle_attach,
      .event_handler = relay_http_event,
      .user_data = &out->headers,
      .keep_alive_enable = true,
      .keep_alive_idle = 5,
      .keep_alive_interval = 5,
      .keep_alive_count = 3,
      .disable_auto_redirect = true,
  };
  out->handle = esp_http_client_init(&config);
  return out->handle != NULL;
}

static void relay_task(void *argument) {
  (void)argument;
#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY
  tk_ir_backoff request_backoff;
  tk_ir_backoff_init(&request_backoff);
#endif
#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
  tk_ir_backoff status_backoff;
  tk_ir_backoff_init(&status_backoff);
#endif
  torget_net_wait();
  relay_http_client poll_client;
  bool poll_created = create_client(&poll_client);
  relay_http_client send_client = {0};
  bool send_created = true;
#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY
  send_created = create_client(&send_client);
#endif
  if (!poll_created || !send_created ||
      tk_ir_derive_keys(TK_VIBEPULSE_DEVICE_KEY,
                        TK_VIBEPULSE_INTERACTION_MAILBOX,
                        &relay_keys) != TK_IR_OK) {
    if (poll_created) esp_http_client_cleanup(poll_client.handle);
    if (send_client.handle != NULL) {
      esp_http_client_cleanup(send_client.handle);
    }
    atomic_fetch_add(&s_failures, 1u);
    atomic_store(&s_running, false);
    vTaskDelete(NULL);
    return;
  }

#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY
  uint64_t next_request_poll_ms = monotonic_ms();
#endif
#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
  uint64_t next_status_poll_ms = monotonic_ms();
  uint64_t next_status_clear_ms = monotonic_ms();
#endif
  atomic_store(&s_running, true);
  ESP_LOGI(TAG, "krypterad reläkanal startad");
  for (;;) {
#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY
    service_verdict(&send_client);
#endif
    uint64_t now_ms = monotonic_ms();
#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY
    if (now_ms >= next_request_poll_ms &&
        tk_ir_backoff_ready(&request_backoff, now_ms)) {
      if (poll_request(&poll_client)) {
        tk_ir_backoff_wifi_recovered(&request_backoff);
        next_request_poll_ms =
            now_ms + TK_IR_POLL_INTERVAL_MS + jitter_ms(500u);
      } else {
        atomic_fetch_add(&s_failures, 1u);
        next_request_poll_ms = now_ms + tk_ir_backoff_fail(
            &request_backoff, now_ms, jitter_ms(TK_IR_BACKOFF_MIN_MS));
      }
    }
    /* A request GET can consume the full HTTP timeout. Recheck the verdict
     * queue before starting the optional status GET so adding live status
     * never doubles the pre-existing worst-case verdict delay. */
    service_verdict(&send_client);
#endif
#if CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY
    if (now_ms >= next_status_poll_ms &&
        tk_ir_backoff_ready(&status_backoff, now_ms)) {
      if (poll_status(&poll_client)) {
        tk_ir_backoff_wifi_recovered(&status_backoff);
        next_status_poll_ms =
            now_ms + TK_IR_STATUS_POLL_INTERVAL_MS + jitter_ms(500u);
      } else {
        atomic_fetch_add(&s_failures, 1u);
        next_status_poll_ms = now_ms + tk_ir_backoff_fail(
            &status_backoff, now_ms, jitter_ms(TK_IR_BACKOFF_MIN_MS));
      }
    }
    if (now_ms >= next_status_clear_ms) {
      clear_stale_status();
      next_status_clear_ms = now_ms + 1000u;
    }
#endif
    vTaskDelay(pdMS_TO_TICKS(TK_IR_LOOP_MS));
  }
}

void tokens_interaction_relay_net_start(void) {
  if (atomic_exchange(&s_started, true)) return;
  tk_ir_retry_init(&verdict_retry);
  tk_ir_backoff_init(&verdict_backoff);
  atomic_store(&s_polls_ok, 0u);
  atomic_store(&s_requests_applied, 0u);
  atomic_store(&s_verdicts_acked, 0u);
  atomic_store(&s_status_polls_ok, 0u);
  atomic_store(&s_status_applied, 0u);
  atomic_store(&s_status_cleared, 0u);
  atomic_store(&s_failures, 0u);
  atomic_store(&s_last_http_status, 0);
  atomic_store(&s_running, false);
#if CONFIG_TK_VIBEPULSE_INTERACTION_RELAY
  s_queue = xQueueCreateStatic(TK_IR_VERDICT_QUEUE_CAP,
                               sizeof(relay_verdict_item), s_queue_storage,
                               &s_queue_control);
  if (s_queue == NULL) {
    atomic_fetch_add(&s_failures, 1u);
    atomic_store(&s_started, false);
    ESP_LOGE(TAG, "krypterad verdict-kö kunde inte starta");
    return;
  }
#endif
  if (xTaskCreate(relay_task, "interaction-relay", 10 * 1024, NULL, 4,
                  NULL) != pdPASS) {
    atomic_fetch_add(&s_failures, 1u);
    atomic_store(&s_started, false);
    ESP_LOGE(TAG, "krypterad interaktionskanal kunde inte starta");
  }
}

tk_interaction_relay_net_status tokens_interaction_relay_net_status(void) {
  return (tk_interaction_relay_net_status){
      .polls_ok = atomic_load(&s_polls_ok),
      .requests_applied = atomic_load(&s_requests_applied),
      .verdicts_acked = atomic_load(&s_verdicts_acked),
      .status_polls_ok = atomic_load(&s_status_polls_ok),
      .status_applied = atomic_load(&s_status_applied),
      .status_cleared = atomic_load(&s_status_cleared),
      .failures = atomic_load(&s_failures),
      .last_http_status = atomic_load(&s_last_http_status),
      .running = atomic_load(&s_running),
  };
}
