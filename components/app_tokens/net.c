/*
 * VibePulse-hämttasken — appens eget nätverk. Tjänsten bor på LAN:et
 * (tools/tokenserver på Macen, vanlig HTTP utan certifikat) och svarar på
 * någon sekund; 30 s-kadensen ger tickern färsk takt utan att störa Macen.
 *
 * TK_TOKENS_URL sätts i secrets.h (Mac:ens LAN-adress är hemlig på samma
 * sätt som WiFi-lösenordet: den beskriver ditt hemnät). Utan definierad URL
 * startar ingen task alls — vyn står ärligt med streck.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "app_tokens.h"
#ifdef ESP_PLATFORM
#include "secrets.h"
#endif
#include "tokens_parse.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "tokens";

#define FETCH_EVERY_MS 30000
#define BODY_MAX 2048

#ifdef TK_TOKENS_URL

static void net_task(void *arg) {
  (void)arg;
  static char body[BODY_MAX]; /* på .bss, inte på taskens stack */
  size_t len;

  torget_net_wait();
  /* Fasförskjutning: alla appars tasker släpps av torget_net_wait i samma
   * ögonblick, och samtidiga hämtningar + första omritningen visade sig
   * kunna svälta internminnet så SPI-flushen till panelen dog i NO_MEM.
   * VibePulse är den tålmodiga appen — den väntar tio sekunder och
   * hamnar sedan i motfas mot Solelkollens 30-sekunderskadens. */
  vTaskDelay(pdMS_TO_TICKS(10000));

  for (;;) {
    tk_tokens t;
    if (torget_http_get(TK_TOKENS_URL, body, sizeof body, &len)
        && tk_tokens_parse(body, len, &t)) {
      torget_ui_lock();
      tokens_apply(&t);
      torget_ui_unlock();
      ESP_LOGI(TAG, "hämtning ok (%.2f Mtok idag, %d sessioner)",
               t.day_tokens / 1e6, t.day_sessions);
    } else {
      ESP_LOGW(TAG, "hämtningen avvisad, värden står kvar");
    }
    /* Misslyckad hämtning gör ingenting: appens tick tänder stale efter
     * två minuter — Macen kan ju vara avstängd, det är inte ett fel. */

    vTaskDelay(pdMS_TO_TICKS(FETCH_EVERY_MS));
  }
}

void tokens_net_start(void) {
  xTaskCreate(net_task, "tokens", 6144, NULL, 5, NULL);
}

#else /* ingen TK_TOKENS_URL i secrets.h */

void tokens_net_start(void) {
  ESP_LOGW(TAG, "TK_TOKENS_URL saknas i secrets.h — VibePulse visar streck");
}

#endif
