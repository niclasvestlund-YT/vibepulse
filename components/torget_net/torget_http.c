#include "torget_http.h"

#include <stdatomic.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "net_source_policy.h"
#include "service_discovery.h"
#include "vibepulse_recovery.h"

static const char *TAG = "torget-http";

static StaticSemaphore_t s_cloud_io_storage;
static SemaphoreHandle_t s_cloud_io;
static portMUX_TYPE s_cloud_io_init_lock = portMUX_INITIALIZER_UNLOCKED;

bool torget_cloud_io_init(void) {
  if (s_cloud_io != NULL) return true;
  taskENTER_CRITICAL(&s_cloud_io_init_lock);
  if (s_cloud_io == NULL) {
    s_cloud_io = xSemaphoreCreateMutexStatic(&s_cloud_io_storage);
  }
  taskEXIT_CRITICAL(&s_cloud_io_init_lock);
  return s_cloud_io != NULL;
}

bool torget_cloud_io_acquire(uint32_t timeout_ms) {
  if (!torget_cloud_io_init()) return false;
  TickType_t ticks = pdMS_TO_TICKS(timeout_ms);
  if (timeout_ms != 0 && ticks == 0) ticks = 1;
  return xSemaphoreTake(s_cloud_io, ticks) == pdTRUE;
}

void torget_cloud_io_release(void) {
  if (s_cloud_io != NULL) (void)xSemaphoreGive(s_cloud_io);
}

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

static bool http_get_timeout(const char *url, char *buf, size_t cap,
                             size_t *len_out, int timeout_ms, bool cloud) {
  body_t body = { .buf = buf, .cap = cap };
  bool gate_held = false;
  bool ok = false;
  esp_http_client_handle_t client = NULL;

  if (!url || !buf || cap == 0 || timeout_ms <= 0) return false;
  if (cloud) {
    gate_held = torget_cloud_io_acquire((uint32_t)timeout_ms);
    if (!gate_held) {
      ESP_LOGW(TAG, "molnhämtning väntade för länge på TLS-grinden");
      return false;
    }
  }

  esp_http_client_config_t cfg = {
    .url = url,
    .method = HTTP_METHOD_GET,
    .event_handler = on_event,
    .user_data = &body,
    .crt_bundle_attach = esp_crt_bundle_attach,
    .timeout_ms = timeout_ms,
    /* Följ en eventuell omdirigering (apex → www och liknande) men inte i
     * all evighet. */
    .max_redirection_count = 3,
  };

  client = esp_http_client_init(&cfg);
  if (!client) goto done;

  /* Content-free post-restart evidence for the local health endpoint. Never
   * send it to a public relay, and never make data delivery depend on this
   * optional diagnostic header. */
  if (!cloud && torget_net_http_stall_recovery_booted() &&
      esp_http_client_set_header(
          client, "X-VibePulse-Recovery-Boot", "http-stall-v1") != ESP_OK) {
    ESP_LOGW(TAG, "kunde inte märka HTTP-stall recovery-boot");
  }

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

done:
  if (client != NULL) esp_http_client_cleanup(client);
  if (gate_held) torget_cloud_io_release();
  return ok;
}

bool torget_http_get(const char *url, char *buf, size_t cap, size_t *len_out) {
  return http_get_timeout(url, buf, cap, len_out, TG_NET_LOCAL_TIMEOUT_MS,
                          false);
}

/*
 * Växlingstillståndet delas av alla hämttasker, och avsiktligt utan lås:
 * fälten är två, båda skrivs bara med hela värden, och en kapad läsning av
 * tidsstämpeln kostar som mest ETT extra LAN-återprov. Ett mutex här hade
 * lagt en låsordning mellan apptaskarna för att skydda en optimering —
 * dyrare än felet det förhindrar. Atomära fält räcker för att undvika
 * odefinierat beteende på de 64 bitarna.
 */
static _Atomic bool s_relay_won;
static _Atomic int64_t s_last_local_try_us;

bool torget_http_get_failover(const char *lan_url, const char *relay_url,
                              char *buf, size_t cap, size_t *len_out) {
  if (!relay_url || !relay_url[0])
    return torget_http_get(lan_url, buf, cap, len_out);

  const int64_t now = esp_timer_get_time();
  tg_net_source_state state = {
    .relay_won = atomic_load(&s_relay_won),
    .last_local_try_us = atomic_load(&s_last_local_try_us),
  };

  tg_net_source source = tg_net_source_first(&state, now);
  const char *url = (source == TG_NET_SOURCE_LOCAL) ? lan_url : relay_url;
  bool ok = http_get_timeout(
      url, buf, cap, len_out, tg_net_source_timeout_ms(&state, source),
      source == TG_NET_SOURCE_RELAY);

  tg_net_source_note(&state, source, ok, now);
  atomic_store(&s_relay_won, state.relay_won);
  atomic_store(&s_last_local_try_us, state.last_local_try_us);

  if (ok || !tg_net_source_may_fall_back(source)) return ok;

  /* LAN föll — reläet är hela poängen med att vara på resa. */
  ESP_LOGI(TAG, "LAN svarade inte, provar reläet");
  ok = http_get_timeout(relay_url, buf, cap, len_out,
                        TG_NET_LOCAL_TIMEOUT_MS, true);
  tg_net_source_note(&state, TG_NET_SOURCE_RELAY, ok, now);
  atomic_store(&s_relay_won, state.relay_won);
  return ok;
}

bool torget_http_get_service(const char *path, const char *configured_url,
                             const char *relay_url, char *buf, size_t cap,
                             size_t *len_out) {
  char discovered[160];
  tg_service_source source = TG_SERVICE_SOURCE_CONFIGURED;
  if (!torget_service_endpoint_url(path, configured_url, discovered,
                                   sizeof discovered, &source)) {
    return false;
  }
  if (source == TG_SERVICE_SOURCE_CONFIGURED ||
      strcmp(discovered, configured_url) == 0) {
    return torget_http_get_failover(configured_url, relay_url,
                                    buf, cap, len_out);
  }

  bool ok = http_get_timeout(discovered, buf, cap, len_out,
                             TG_NET_REPROBE_TIMEOUT_MS, false);
  torget_service_note_result(source, discovered, ok);
  if (ok) return true;

  /* The failed origin is now backed off. One immediate query may select a
   * second advertising Mac/PC without waiting for the next poll interval. */
  char alternate[160];
  tg_service_source alternate_source = TG_SERVICE_SOURCE_CONFIGURED;
  if (torget_service_endpoint_url(path, configured_url, alternate,
                                  sizeof alternate, &alternate_source) &&
      alternate_source == TG_SERVICE_SOURCE_DISCOVERED &&
      strcmp(alternate, discovered) != 0 &&
      strcmp(alternate, configured_url) != 0) {
    ok = http_get_timeout(alternate, buf, cap, len_out,
                          TG_NET_REPROBE_TIMEOUT_MS, false);
    torget_service_note_result(alternate_source, alternate, ok);
    if (ok) return true;
  }

  ESP_LOGI(TAG, "lokala VibePulse-värdar svarade inte, provar reservvägen");
  return torget_http_get_failover(configured_url, relay_url,
                                  buf, cap, len_out);
}
