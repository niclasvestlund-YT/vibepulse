#include "torget_http.h"

#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"

static const char *TAG = "torget-http";

typedef struct {
  char *buf;
  size_t cap;
  size_t len;
  bool overflow;
} body_t;

static esp_err_t on_event(esp_http_client_event_t *evt) {
  if (evt->event_id != HTTP_EVENT_ON_DATA) return ESP_OK;

  body_t *b = (body_t *)evt->user_data;
  if (!b || b->overflow) return ESP_OK;

  if (b->len + (size_t)evt->data_len >= b->cap) {
    b->overflow = true;
    return ESP_OK;
  }
  memcpy(b->buf + b->len, evt->data, (size_t)evt->data_len);
  b->len += (size_t)evt->data_len;
  return ESP_OK;
}

bool torget_http_get(const char *url, char *buf, size_t cap, size_t *len_out) {
  body_t body = { .buf = buf, .cap = cap };

  esp_http_client_config_t cfg = {
    .url = url,
    .method = HTTP_METHOD_GET,
    .event_handler = on_event,
    .user_data = &body,
    .crt_bundle_attach = esp_crt_bundle_attach,
    .timeout_ms = 10000,
    /* Följ en eventuell omdirigering (apex → www och liknande) men inte i
     * all evighet. */
    .max_redirection_count = 3,
  };

  esp_http_client_handle_t client = esp_http_client_init(&cfg);
  if (!client) return false;

  bool ok = false;
  esp_err_t err = esp_http_client_perform(client);
  int status = esp_http_client_get_status_code(client);

  if (err != ESP_OK) {
    ESP_LOGW(TAG, "hämtning misslyckades: %s (%s)", esp_err_to_name(err), url);
  } else if (status != 200) {
    ESP_LOGW(TAG, "oväntad statuskod %d (%s)", status, url);
  } else if (body.overflow) {
    ESP_LOGW(TAG, "kroppen större än %u byte, avvisad (%s)", (unsigned)cap, url);
  } else {
    buf[body.len] = '\0';
    if (len_out) *len_out = body.len;
    ok = true;
  }

  esp_http_client_cleanup(client);
  return ok;
}
