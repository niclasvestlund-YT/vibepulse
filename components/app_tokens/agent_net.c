/*
 * Agentstatus från VibePulse-tjänsten. En HTTP-klient återanvänds så länge
 * samma upptäckta/configurerade värd gäller; fel lämnar senaste goda UI-status.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_http_client.h"
#include "esp_log.h"

#include "agent_net_policy.h"
#include "agent_status_parse.h"
#include "poll_backoff_policy.h"
#include "app_tokens.h"
#include "secrets.h"
#include "torget.h"
#include "service_discovery.h"

static const char *TAG = "agent-net";

#define AGENT_POLL_MS 1000
/* OBS-13: a dead service used to get this poll every second, forever --
 * 86 400 connect attempts a day. Consecutive misses now double the wait
 * up to thirty seconds; one success resets it. */
#define AGENT_POLL_CAP_MS 30000
#define AGENT_LOG_EVERY_MS 30000
/* A v2 Needs You item adds a 2 KiB snapshot plus the bounded 1 KiB
 * canonical-view buffer and SHA/cJSON call frames to the poll path. The old
 * 6 KiB budget overflowed on the ESP32-S3 as soon as the first real Codex
 * approval arrived. Keep explicit headroom for the strict parser: this task
 * may die while owning UI work immediately after a successful parse. */
#define AGENT_TASK_STACK_BYTES (10 * 1024)

#ifdef TK_AGENT_STATUS_URL

static tk_agent_http_response response;
static portMUX_TYPE s_origin_lock = portMUX_INITIALIZER_UNLOCKED;
static char s_direct_origin[64];

static bool origin_from_url(const char *url, char *origin, size_t cap) {
  if (!url || strncmp(url, "http://", 7) != 0 || !origin || cap == 0)
    return false;
  const char *path = strchr(url + 7, '/');
  if (!path) return false;
  size_t len = (size_t)(path - url);
  if (len == 0 || len >= cap) return false;
  memcpy(origin, url, len);
  origin[len] = '\0';
  return true;
}

static void remember_direct_origin(const char *url) {
  char origin[sizeof s_direct_origin] = {0};
  if (!origin_from_url(url, origin, sizeof origin)) return;
  taskENTER_CRITICAL(&s_origin_lock);
  memcpy(s_direct_origin, origin, sizeof s_direct_origin);
  s_direct_origin[sizeof s_direct_origin - 1] = '\0';
  taskEXIT_CRITICAL(&s_origin_lock);
}

bool tokens_agent_direct_origin(char *origin, size_t cap) {
  if (!origin || cap == 0) return false;
  char selected[sizeof s_direct_origin] = {0};
  taskENTER_CRITICAL(&s_origin_lock);
  memcpy(selected, s_direct_origin, sizeof selected);
  taskEXIT_CRITICAL(&s_origin_lock);
  if (!selected[0] &&
      !origin_from_url(TK_AGENT_STATUS_URL, selected, sizeof selected)) {
    return false;
  }
  int written = snprintf(origin, cap, "%s", selected);
  return written >= 0 && (size_t)written < cap;
}

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

static const char *fetch_result_name(tk_agent_http_fetch_result fetch) {
  switch (fetch) {
  case TK_AGENT_HTTP_FETCH_OK: return "ok";
  case TK_AGENT_HTTP_FETCH_IO_ERROR: return "IO-fel (öppna/läsa)";
  case TK_AGENT_HTTP_FETCH_OVERFLOW: return "överflöde";
  }
  return "okänt";
}

static void log_rejection(tk_agent_http_fetch_result fetch, bool parsed) {
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
    ESP_LOGW(TAG, "agentstatus avvisad: svar större än %u byte",
             (unsigned)(TK_AGENT_HTTP_BODY_CAP - 1));
  } else if (fetch != TK_AGENT_HTTP_FETCH_OK) {
    /* OBS-12: the real three-valued result, not a collapsed ESP_FAIL. */
    ESP_LOGW(TAG, "agentstatus avvisad: transportfel, %s",
             fetch_result_name(fetch));
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

  esp_http_client_handle_t client = NULL;
  char client_url[160] = {0};
  tg_service_source client_source = TG_SERVICE_SOURCE_CONFIGURED;
  tk_poll_backoff backoff;
  tk_poll_backoff_init(&backoff, AGENT_POLL_MS, AGENT_POLL_CAP_MS);

  ESP_LOGI(TAG, "agentstatuspollning startad");
  for (;;) {
    char selected_url[sizeof client_url];
    tg_service_source selected_source = TG_SERVICE_SOURCE_CONFIGURED;
    if (!torget_service_endpoint_url(
            "/api/agent-status", TK_AGENT_STATUS_URL,
            selected_url, sizeof selected_url, &selected_source)) {
      if (tk_poll_backoff_note(&backoff, false)) {
        ESP_LOGW(TAG, "agentstatus: ingen adress att polla, %" PRIu32
                      " fel i rad — provar var %" PRIu32 " ms",
                 backoff.streak, tk_poll_backoff_delay_ms(&backoff));
      }
      vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
      continue;
    }
    if (!client || strcmp(client_url, selected_url) != 0) {
      if (client) esp_http_client_cleanup(client);
      snprintf(client_url, sizeof client_url, "%s", selected_url);
      client_source = selected_source;
      esp_http_client_config_t cfg = {
        .url = client_url,
        .timeout_ms = 2500,
        .keep_alive_enable = true,
        .keep_alive_idle = 5,
        .keep_alive_interval = 5,
        .keep_alive_count = 3,
        .event_handler = status_http_event,
        .user_data = &response,
      };
      client = esp_http_client_init(&cfg);
      if (!client) {
        /* OBS-12: keep trying (a fresh client next pass), on the backoff
         * ladder -- the task used to have nothing better than a fixed
         * one-second retry here. */
        ESP_LOGW(TAG, "agentstatus kunde inte skapa HTTP-klient");
        client_url[0] = '\0';
        (void)tk_poll_backoff_note(&backoff, false);
        vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
        continue;
      }
    }
    tk_agent_http_fetch_result fetch =
        tk_agent_http_fetch_bounded(client, &response, &status_http_io);

    tk_agent_snapshot snapshot;
    bool transport_ok = fetch == TK_AGENT_HTTP_FETCH_OK;
    bool parsed = false;
    if (transport_ok && response.status == 200 && !response.overflow) {
      parsed = tk_agent_status_parse(response.body, response.len, &snapshot);
    }
    bool accepted = tk_agent_http_response_can_apply(
        &response, transport_ok, parsed);
    bool host_ok = transport_ok && response.status == 200 &&
                   !response.overflow;
    torget_service_note_result(client_source, client_url, host_ok);
    if (accepted) {
      remember_direct_origin(client_url);
      torget_ui_lock();
      tokens_apply_agent_status(&snapshot);
      torget_ui_unlock();
    } else {
      log_rejection(fetch, parsed);
      if (client_source == TG_SERVICE_SOURCE_DISCOVERED && !host_ok) {
        esp_http_client_cleanup(client);
        client = NULL;
        client_url[0] = '\0';
      }
    }

    /* Transitions only: the slowdown steps and the recovery, never every
     * miss (the rejection log above is already throttled). The feed counts
     * as a miss unless the response was APPLIED: a host that answers 200
     * with a body the parser rejects is as useless to the screen as a dead
     * one, and hammering it every second changes nothing (host_ok stays
     * the service-discovery signal only). */
    uint32_t streak_before = backoff.streak;
    if (tk_poll_backoff_note(&backoff, accepted)) {
      if (accepted) {
        ESP_LOGI(TAG, "agentstatus svarar igen efter %" PRIu32 " missar",
                 streak_before);
      } else {
        ESP_LOGW(TAG, "agentstatus: %" PRIu32 " missar i rad — pollar var "
                      "%" PRIu32 " ms tills tjänsten svarar",
                 backoff.streak, tk_poll_backoff_delay_ms(&backoff));
      }
    }
    vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
  }
}

void tokens_agent_net_start(void) {
  if (xTaskCreate(agent_net_task, "agent-status", AGENT_TASK_STACK_BYTES,
                  NULL, 5, NULL) != pdPASS) {
    ESP_LOGE(TAG, "agentstatus-tasken kunde inte starta");
  }
}

#else

bool tokens_agent_direct_origin(char *origin, size_t cap) {
  if (!origin || cap == 0) return false;
#ifdef TK_VIBEPULSE_BASE_URL
  int written = snprintf(origin, cap, "%s", TK_VIBEPULSE_BASE_URL);
  return written >= 0 && (size_t)written < cap;
#else
  origin[0] = '\0';
  return false;
#endif
}

void tokens_agent_net_start(void) {
  ESP_LOGW(TAG, "TK_AGENT_STATUS_URL saknas i secrets.h — agentstatus avstängd");
}

#endif
