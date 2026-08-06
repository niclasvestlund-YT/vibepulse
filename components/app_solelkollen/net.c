/*
 * Solelkollens hämttask — appens eget nätverk (P25-krav 2: hämtning bor i
 * appen, inte i plattformens main). Targetets motsvarighet till simulatorns
 * fixtureläsning: samma sg_glance_parse() tuggar båda källorna.
 *
 * Kadensen: /api/glance var 30:e sekund; /api/glance-sverige var 60:e cykel
 * = 30 min, samma takt som CDN-cachen på routen (settlement rör sig några
 * gånger per dygn). Misslyckad hämtning gör ingenting alls: gamla värden
 * står kvar och appens tick tänder stale-markeringen efter två minuter.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "app_solelkollen.h"
#include "glance_parse.h"
#include "secrets.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "solelkollen";

#define FETCH_EVERY_MS       30000
#define SVERIGE_EVERY_CYCLES 60

/* Svaren är ~150 byte per API:ts egen designkommentar; 1 kB är spärren. */
#define BODY_MAX 1024

/* Sverige-routen bor bredvid glance-routen. Egen define i secrets.h vinner
 * (t.ex. mot en lokal dev-server); annars härleds prod-adressen här. */
#ifndef SG_SVERIGE_URL
#define SG_SVERIGE_URL "https://solelkollen.se/api/glance-sverige"
#endif

static void net_task(void *arg) {
  (void)arg;
  static char body[BODY_MAX]; /* på .bss, inte på taskens stack */
  size_t len;

  torget_net_wait();

  for (unsigned cycle = 0;; cycle++) {
    if (cycle % SVERIGE_EVERY_CYCLES == 0) {
      sg_sverige sve;
      if (torget_http_get(SG_SVERIGE_URL, body, sizeof body, &len)
          && sg_sverige_parse(body, len, &sve)) {
        torget_ui_lock();
        solelkollen_apply_sverige(&sve);
        torget_ui_unlock();
        ESP_LOGI(TAG, "sverige ok (avräknat %s, %.1f GWh)", sve.day, sve.day_gwh);
      }
      /* Misslyckas den: strecken eller senaste goda värdet står kvar, och
       * nästa försök kommer om en halvtimme. Datumet i vyn daterar sig själv. */
    }

    sg_glance g;
    char day[11] = { 0 };
    if (torget_http_get(SG_GLANCE_URL, body, sizeof body, &len)
        && sg_glance_parse(body, len, &g, day, sizeof day)) {
      torget_ui_lock();
      solelkollen_apply_glance(&g);
      torget_ui_unlock();
      ESP_LOGI(TAG, "hämtning ok (dag %s, %.2f kr, %d W)", day, g.kr, g.w);
    } else {
      ESP_LOGW(TAG, "hämtningen avvisad, värden står kvar");
    }

    vTaskDelay(pdMS_TO_TICKS(FETCH_EVERY_MS));
  }
}

void solelkollen_net_start(void) {
  xTaskCreate(net_task, "solelkollen", 6144, NULL, 5, NULL);
}
