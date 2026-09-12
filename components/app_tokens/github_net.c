/*
 * Optional GitHub LAN feed. GitHub itself is polled by tokenserver so the
 * display never owns public-API TLS, rate limits or retry state.
 */
#include <inttypes.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "app_tokens.h"
#include "app_tokens_config.h"
#include "github_status_parse.h"
#include "poll_backoff_policy.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "github-net";

#define GITHUB_FETCH_EVERY_MS 30000
/* OBS-13: same shape as the tokens poller -- a dead tokenserver is not
 * chased every 30 s all day by an optional feed. */
#define GITHUB_FETCH_MAX_MS 300000
#define GITHUB_BODY_MAX 768

#ifndef TK_GITHUB_URL
#define TK_GITHUB_URL NULL
#endif

static void github_net_task(void *arg) {
  (void)arg;
  static char body[GITHUB_BODY_MAX];
  size_t len;

  tk_poll_backoff backoff;
  tk_poll_backoff_init(&backoff, GITHUB_FETCH_EVERY_MS, GITHUB_FETCH_MAX_MS);

  torget_net_wait();
  /* Separate this request from quotas (10 s), Max Tracker (15 s) and the
   * one-second agent feed. GitHub can wait; it must never contend with them. */
  vTaskDelay(pdMS_TO_TICKS(20000));

  for (;;) {
    tk_github_status status;
    bool fetched = torget_http_get_service("/api/github", TK_GITHUB_URL,
                                           TK_GITHUB_RELAY_URL,
                                           body, sizeof body, &len) &&
                   tk_github_status_parse(body, len, &status);
    if (fetched) {
      torget_ui_lock();
      tokens_apply_github(&status);
      torget_ui_unlock();
    } else {
      ESP_LOGW(TAG, "GitHub-flödet avvisades; Claude/Codex fortsätter");
    }
    /* Transitions only, like the other pollers: the slowdown steps and
     * the recovery, never every miss. */
    uint32_t streak_before = backoff.streak;
    if (tk_poll_backoff_note(&backoff, fetched)) {
      if (fetched) {
        ESP_LOGI(TAG, "GitHub-flödet svarar igen efter %" PRIu32 " missar",
                 streak_before);
      } else {
        ESP_LOGW(TAG, "GitHub-flödet: %" PRIu32 " missar i rad — hämtar var %"
                      PRIu32 " s tills tjänsten svarar",
                 backoff.streak, tk_poll_backoff_delay_ms(&backoff) / 1000);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
  }
}

void tokens_github_net_start(void) {
  if (!tk_labs_active(TK_LABS_GITHUB) && !tk_labs_active(TK_LABS_STAR_POPUP)) {
    ESP_LOGI(TAG, "GitHub-sida och stjärnnotiser är avstängda");
    return;
  }
  if (xTaskCreate(github_net_task, "github", 5120, NULL, 4, NULL) != pdPASS)
    ESP_LOGE(TAG, "GitHub-tasken kunde inte starta");
}
