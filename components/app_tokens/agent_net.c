/*
 * Agentstatus från VibePulse-tjänsten. En enda HTTP-klient återanvänds av
 * den långlivade tasken; fel lämnar alltid den senaste goda UI-statusen.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_http_client.h"
#include "esp_log.h"

#include "agent_net_policy.h"
#include "agent_status_parse.h"
#include "app_tokens.h"
#include "secrets.h"
#include "torget.h"

static const char *TAG = "agent-net";

#define AGENT_POLL_MS 1000
#define AGENT_LOG_EVERY_MS 30000

#ifdef TK_AGENT_STATUS_URL

static tk_agent_http_response response;

static esp_err_t status_http_event(esp_http_client_event_t *event) {
  /* ESP-IDF ignorerar callbackens returvärde för ON_DATA. Den bounded
   * read-loopen äger därför all kopiering och kan stänga socketen säkert. */
  if (!event || (event->event_id == HTTP_EVENT_ON_DATA &&
                 !event->user_data)) return ESP_FAIL;
  return ESP_OK;
}

static int status_http_open(void *context) {
  return (int)esp_http_client_open((esp_http_client_handle_t)context, 0);
}

static int64_t status_http_fetch_headers(void *context) {
  return esp_http_client_fetch_headers((esp_http_client_handle_t)context);
}

static int status_http_status(void *context) {
  return esp_http_client_get_status_code((esp_http_client_handle_t)context);
}

static int status_http_read(void *context, char *buffer, int capacity) {
  return esp_http_client_read((esp_http_client_handle_t)context, buffer,
                              capacity);
}

static bool status_http_complete(void *context) {
  return esp_http_client_is_complete_data_received(
      (esp_http_client_handle_t)context);
}

static void status_http_close(void *context) {
  (void)esp_http_client_close((esp_http_client_handle_t)context);
}

static const tk_agent_http_io status_http_io = {
  .open = status_http_open,
  .fetch_headers = status_http_fetch_headers,
  .get_status = status_http_status,
  .read = status_http_read,
  .is_complete = status_http_complete,
  .close = status_http_close,
};

static void log_rejection(esp_err_t err, bool parsed) {
  static bool has_logged;
  static TickType_t last_log_tick;
  TickType_t now = xTaskGetTickCount();
  if (has_logged &&
      now - last_log_tick < pdMS_TO_TICKS(AGENT_LOG_EVERY_MS)) {
    return;
  }
  has_logged = true;
  last_log_tick = now;

  if (response.overflow) {
    ESP_LOGW(TAG, "agentstatus avvisad: svar större än 1535 byte");
  } else if (err != ESP_OK) {
    ESP_LOGW(TAG, "agentstatus avvisad: transportfel %s",
             esp_err_to_name(err));
  } else if (response.status != 200) {
    ESP_LOGW(TAG, "agentstatus avvisad: HTTP %d", response.status);
  } else if (!parsed) {
    ESP_LOGW(TAG, "agentstatus avvisad: ogiltigt format");
  }
}

static void agent_net_task(void *arg) {
  (void)arg;

  torget_net_wait();
  vTaskDelay(pdMS_TO_TICKS(3000));

  esp_http_client_config_t cfg = {
    .url = TK_AGENT_STATUS_URL,
    .timeout_ms = 2500,
    .keep_alive_enable = true,
    .keep_alive_idle = 5,
    .keep_alive_interval = 5,
    .keep_alive_count = 3,
    .event_handler = status_http_event,
    .user_data = &response,
  };
  esp_http_client_handle_t client = esp_http_client_init(&cfg);
  if (!client) {
    ESP_LOGE(TAG, "agentstatus kunde inte skapa HTTP-klient");
    vTaskDelete(NULL);
    return;
  }

  ESP_LOGI(TAG, "agentstatuspollning startad");
  for (;;) {
    tk_agent_http_fetch_result fetch =
        tk_agent_http_fetch_bounded(client, &response, &status_http_io);
    esp_err_t err = fetch == TK_AGENT_HTTP_FETCH_OK ? ESP_OK : ESP_FAIL;

    tk_agent_snapshot snapshot;
    bool transport_ok = err == ESP_OK;
    bool parsed = false;
    if (transport_ok && response.status == 200 && !response.overflow) {
      parsed = tk_agent_status_parse(response.body, response.len, &snapshot);
    }
    if (tk_agent_http_response_can_apply(&response, transport_ok, parsed)) {
      torget_ui_lock();
      tokens_apply_agent_status(&snapshot);
      torget_ui_unlock();
    } else {
      log_rejection(err, parsed);
    }

    vTaskDelay(pdMS_TO_TICKS(AGENT_POLL_MS));
  }
}

void tokens_agent_net_start(void) {
  xTaskCreate(agent_net_task, "agent-status", 6144, NULL, 5, NULL);
}

#else

void tokens_agent_net_start(void) {
  ESP_LOGW(TAG, "TK_AGENT_STATUS_URL saknas i secrets.h — agentstatus avstängd");
}

#endif
