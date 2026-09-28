/*
 * Optional Lovable LAN feed. Lovable itself (OAuth, MCP, TLS, retries) is
 * handled by the tokenserver on the Mac; the panel only ever reads the flat
 * numbers payload on /api/lovable and never holds a Lovable credential.
 * LAN only by design: no relay URL is derived for this feed.
 */
#include <inttypes.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "app_tokens.h"
#include "app_tokens_config.h"
#include "lovable_status_parse.h"
#include "poll_backoff_policy.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "lovable-net";

/* The Mac polls Lovable every five minutes; a minute here keeps the age line
 * honest without adding load to anything. */
#define LOVABLE_FETCH_EVERY_MS 60000
#define LOVABLE_FETCH_MAX_MS 300000
#define LOVABLE_BODY_MAX 512

#ifndef TK_LOVABLE_URL
#define TK_LOVABLE_URL NULL
#define TK_LOVABLE_URL_CONFIGURED 0
#else
#define TK_LOVABLE_URL_CONFIGURED 1
#endif

static void lovable_net_task(void *arg) {
  (void)arg;
  static char body[LOVABLE_BODY_MAX];
  size_t len;

  tk_poll_backoff backoff;
  tk_poll_backoff_init(&backoff, LOVABLE_FETCH_EVERY_MS, LOVABLE_FETCH_MAX_MS);

  torget_net_wait();
#if !TK_LOVABLE_URL_CONFIGURED
  ESP_LOGI(TAG, "TK_LOVABLE_URL saknas i secrets.h — Lovable-flödet hämtas "
                "bara från en annonserad tokenserver");
#endif
  /* After quotas (10 s), Max Tracker (15 s) and GitHub (20 s). */
  vTaskDelay(pdMS_TO_TICKS(25000));

  for (;;) {
    tk_lovable_status status;
    bool fetched = torget_http_get_service("/api/lovable", TK_LOVABLE_URL,
                                           NULL, body, sizeof body, &len) &&
                   tk_lovable_status_parse(body, len, &status);
    if (fetched) {
      torget_ui_lock();
      tokens_apply_lovable(&status);
      torget_ui_unlock();
    } else {
      ESP_LOGW(TAG, "Lovable-flödet avvisades; Claude/Codex fortsätter");
    }
    uint32_t streak_before = backoff.streak;
    if (tk_poll_backoff_note(&backoff, fetched)) {
      if (fetched) {
        ESP_LOGI(TAG, "Lovable-flödet svarar igen efter %" PRIu32 " missar",
                 streak_before);
      } else {
        ESP_LOGW(TAG, "Lovable-flödet: %" PRIu32 " missar i rad — hämtar var %"
                      PRIu32 " s", backoff.streak,
                 tk_poll_backoff_delay_ms(&backoff) / 1000);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
  }
}

void tokens_lovable_net_start(void) {
  if (!tk_labs_active(TK_LABS_LOVABLE)) {
    ESP_LOGI(TAG, "Lovable-sidan är avstängd");
    return;
  }
  if (xTaskCreate(lovable_net_task, "lovable", 5120, NULL, 4, NULL) != pdPASS)
    ESP_LOGE(TAG, "Lovable-tasken kunde inte starta");
}
