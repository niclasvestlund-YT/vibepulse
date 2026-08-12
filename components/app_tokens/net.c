/*
 * VibePulse-hämttaskerna — appens eget nätverk. Tjänsten bor på LAN:et
 * (tools/tokenserver på Macen, vanlig HTTP utan certifikat). Två oberoende
 * tasker delar filen: net_task pollar /api/tokens var 30:e sekund för
 * tickern, max_tracker_task pollar /api/max-tracker var 5:e minut för
 * kvothistoriken (den ändras i dagstakt, ingen anledning att jaga Macen).
 *
 * TK_TOKENS_URL och TK_MAX_TRACKER_URL sätts oberoende i secrets.h (Mac:ens
 * LAN-adress är hemlig på samma sätt som WiFi-lösenordet: den beskriver ditt
 * hemnät). Utan en definierad URL startar motsvarande task inte alls — vyn
 * står ärligt med streck.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "app_tokens.h"
#ifdef ESP_PLATFORM
#include "secrets.h"
#endif
#include "max_tracker_parse.h"
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

#endif /* TK_TOKENS_URL */

/*
 * Max Tracker-hämttasken — samma glance-mönster som net_task ovan men eget
 * fönster (TK_MAX_TRACKER_URL kan sättas oberoende av TK_TOKENS_URL) och
 * egen kadens: historiken ändras i dagstakt, så fem minuter är gott om
 * marginal utan att jaga Macen i onödan.
 *
 * MT_BODY_MAX 8192 mot det upplösta 20-veckorskontraktet (140 [pct,lvl]-par
 * plus aggregat): den riktiga fixturen max-tracker-full.json väger 3275
 * byte, och ett strukturellt värsta-fall (alla fält på sina tak, kortaste
 * fälten längsta tillåtna sträng) landar kring 3,5 kB — bufferten har alltså
 * över 2x marginal kvar även mot ett konstruerat värsta fall.
 */
#define MT_FETCH_EVERY_MS 300000
#define MT_BODY_MAX 8192

#ifdef TK_MAX_TRACKER_URL

static void max_tracker_task(void *arg) {
  (void)arg;
  static char body[MT_BODY_MAX]; /* på .bss, inte på taskens stack */
  size_t len;

  torget_net_wait();
  /* Samma fasförskjutningsskäl som net_task: torget_net_wait släpper alla
   * appars tasker samtidigt. Max Tracker väntar femton sekunder — förbi
   * både Tokenmätarens tio och agentstatusens tre — så de tre hämtningarna
   * aldrig konkurrerar om internminnet på en och samma gång. */
  vTaskDelay(pdMS_TO_TICKS(15000));

  for (;;) {
    tk_max_tracker t;
    if (torget_http_get(TK_MAX_TRACKER_URL, body, sizeof body, &len)
        && tk_max_tracker_parse(body, len, &t)) {
      torget_ui_lock();
      tokens_apply_max_tracker(&t);
      torget_ui_unlock();
      ESP_LOGI(TAG, "max tracker-hämtning ok (streak %d dagar)",
               t.coding_streak_days);
    } else {
      ESP_LOGW(TAG, "max tracker-hämtningen avvisad, värden står kvar");
    }
    /* Misslyckad hämtning gör ingenting: skärmens egen tick tänder stale
     * efter två minuter — Macen kan ju vara avstängd, det är inte ett fel. */

    vTaskDelay(pdMS_TO_TICKS(MT_FETCH_EVERY_MS));
  }
}

#endif /* TK_MAX_TRACKER_URL */

void tokens_net_start(void) {
#ifdef TK_TOKENS_URL
  xTaskCreate(net_task, "tokens", 6144, NULL, 5, NULL);
#else
  ESP_LOGW(TAG, "TK_TOKENS_URL saknas i secrets.h — VibePulse visar streck");
#endif

#ifdef TK_MAX_TRACKER_URL
  xTaskCreate(max_tracker_task, "max-tracker", 6144, NULL, 5, NULL);
#else
  ESP_LOGW(TAG,
           "TK_MAX_TRACKER_URL saknas i secrets.h — Max Tracker visar streck");
#endif
}
