/* Optional LAN-only Grok feed. Credentials and source collection stay on
 * the computer; the panel reads only strict /api/grok snapshots. */
#include <inttypes.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "app_tokens.h"
#include "app_tokens_config.h"
#include "grok_status_parse.h"
#include "poll_backoff_policy.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "grok-net";

/* Bot activity is polled frequently; failed reads back off independently. */
#define GROK_FETCH_EVERY_MS 15000
#define GROK_FETCH_MAX_MS 120000
#define GROK_BODY_MAX 768

#ifndef TK_GROK_URL
#define TK_GROK_URL NULL
#define TK_GROK_URL_CONFIGURED 0
#else
#define TK_GROK_URL_CONFIGURED 1
#endif

static void grok_net_task(void *arg) {
  (void)arg;
  static char body[GROK_BODY_MAX];
  size_t len;

  tk_poll_backoff backoff;
  tk_poll_backoff_init(&backoff, GROK_FETCH_EVERY_MS, GROK_FETCH_MAX_MS);

  torget_net_wait();
#if !TK_GROK_URL_CONFIGURED
  ESP_LOGI(TAG, "TK_GROK_URL saknas i secrets.h — Grok-flödet hämtas "
                "bara från en annonserad tokenserver");
#endif
  /* After quotas (10 s), Max Tracker (15 s) and GitHub (20 s). */
  vTaskDelay(pdMS_TO_TICKS(30000));

  for (;;) {
    tk_grok_status status;
    bool fetched = torget_http_get_service("/api/grok", TK_GROK_URL,
                                           NULL, body, sizeof body, &len) &&
                   tk_grok_status_parse(body, len, &status);
    if (fetched) {
      torget_ui_lock();
      tokens_apply_grok(&status);
      torget_ui_unlock();
    } else {
      ESP_LOGW(TAG, "Grok-flödet avvisades; Claude/Codex fortsätter");
    }
    uint32_t streak_before = backoff.streak;
    if (tk_poll_backoff_note(&backoff, fetched)) {
      if (fetched) {
        ESP_LOGI(TAG, "Grok-flödet svarar igen efter %" PRIu32 " missar",
                 streak_before);
      } else {
        ESP_LOGW(TAG, "Grok-flödet: %" PRIu32 " missar i rad — hämtar var %"
                      PRIu32 " s", backoff.streak,
                 tk_poll_backoff_delay_ms(&backoff) / 1000);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
  }
}

void tokens_grok_net_start(void) {
  if (!TK_GROK_SCREEN_ENABLED) {
    ESP_LOGI(TAG, "Grok-sidan är avstängd");
    return;
  }
  if (xTaskCreate(grok_net_task, "grok", 5120, NULL, 4, NULL) != pdPASS)
    ESP_LOGE(TAG, "Grok-tasken kunde inte starta");
}
