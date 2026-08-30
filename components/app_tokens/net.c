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

#include <stdatomic.h>

#include "esp_log.h"

#include "app_tokens.h"
#include "app_tokens_config.h"
#ifdef ESP_PLATFORM
#include "secrets.h"
#endif
#include "max_tracker_parse.h"
#include "tokens_parse.h"
#include "tokens_net_recovery_policy.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "tokens";

#define FETCH_EVERY_MS 30000
#define BODY_MAX 2048
#define RECOVERY_CHECK_MS 5000
#define TOKENS_STALE_AFTER_US (120LL * 1000000LL)

/* Each state transition can be observed one watchdog tick late. Keep the
 * worst-case staged deadline strictly inside the UI stale boundary. */
#if TK_TOKENS_HTTP_STALL_US + TK_TOKENS_HTTP_RESTART_GRACE_US + \
        (2LL * RECOVERY_CHECK_MS * 1000LL) >= TOKENS_STALE_AFTER_US
#error "VibePulse HTTP recovery no longer fits before the stale boundary"
#endif

#ifdef TK_TOKENS_URL

static _Atomic bool s_tokens_has_success;
static _Atomic int64_t s_tokens_last_success_us;
static _Atomic int64_t s_tokens_last_recovery_us;
static TaskHandle_t s_tokens_task;
static const char *const s_tokens_relay_url = TK_TOKENS_RELAY_URL;

static void note_tokens_success(void) {
  atomic_store(&s_tokens_last_success_us, torget_now_us());
  atomic_store(&s_tokens_last_recovery_us, 0);
  atomic_store(&s_tokens_has_success, true);
}

static void recovery_task(void *arg) {
  (void)arg;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(RECOVERY_CHECK_MS));
    tk_tokens_net_recovery_state state = {
      .has_success = atomic_load(&s_tokens_has_success),
      .last_success_us = atomic_load(&s_tokens_last_success_us),
      .last_recovery_us = atomic_load(&s_tokens_last_recovery_us),
    };
    int64_t now_us = torget_now_us();
    bool relay_configured =
        s_tokens_relay_url != NULL && s_tokens_relay_url[0] != '\0';
    tk_tokens_net_recovery_action action =
        tk_tokens_net_recovery_action_for(
            &state, now_us, torget_wifi_signal_bars() > 0,
            relay_configured);
    if (action == TK_TOKENS_NET_RECOVERY_RECYCLE_WIFI) {
      ESP_LOGW(TAG, "inga färska VibePulse-svar; återställer "
                    "WiFi-transporten före stale-gränsen");
      if (torget_net_recover_http_stall()) {
        atomic_store(&s_tokens_last_recovery_us, now_us);
        /* Wake the quota task as soon as disconnect has unwound its current
         * attempt. Its loop waits for live IP before retrying, rather than
         * spending most of the remaining freshness margin asleep. */
        if (s_tokens_task != NULL) xTaskNotifyGive(s_tokens_task);
      }
    } else if (action == TK_TOKENS_NET_RECOVERY_RESTART_DEVICE) {
      ESP_LOGE(TAG, "WiFi-recycle gav ingen färsk VibePulse-data; "
                    "startar om enheten en gång");
      torget_net_restart_http_stall();
    }
  }
}

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
    /* The recovery wake can arrive before reassociation completes. Wait for
     * live IP here on every pass so the immediate retry is not spent while
     * the station is still disconnected. */
    torget_net_wait();
    tk_tokens t;
    if (torget_http_get_service("/api/tokens", TK_TOKENS_URL,
                                TK_TOKENS_RELAY_URL,
                                body, sizeof body, &len)
        && tk_tokens_parse(body, len, &t)) {
      torget_ui_lock();
      tokens_apply(&t);
      torget_ui_unlock();
      /* OTA-annonsen till plattformen — utanför UI-låset, den rör inget UI
       * själv utan bara tjänstens atomära annonsminne. */
      torget_update_available(
          t.has_ota_available_version ? t.ota_available_version : NULL);
      note_tokens_success();
      ESP_LOGI(TAG, "hämtning ok (%.2f Mtok idag, %d sessioner)",
               t.day_tokens / 1e6, t.day_sessions);
    } else {
      ESP_LOGW(TAG, "hämtningen avvisad, värden står kvar");
    }
    /* Misslyckad hämtning gör ingenting: appens tick tänder stale efter
     * två minuter — Macen kan ju vara avstängd, det är inte ett fel. */

    /* The recovery task can interrupt this sleep after a station recycle.
     * A notification delivered while HTTP is still unwinding is retained and
     * makes the next retry immediate. */
    (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(FETCH_EVERY_MS));
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
    if (torget_http_get_service("/api/max-tracker", TK_MAX_TRACKER_URL,
                                TK_MAX_TRACKER_RELAY_URL,
                                body, sizeof body, &len)
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
  atomic_store(&s_tokens_has_success, false);
  atomic_store(&s_tokens_last_success_us, 0);
  atomic_store(&s_tokens_last_recovery_us, 0);
  s_tokens_task = NULL;
  if (xTaskCreate(net_task, "tokens", 6144, NULL, 5,
                  &s_tokens_task) != pdPASS) {
    s_tokens_task = NULL;
    ESP_LOGE(TAG, "VibePulse-hämttasken kunde inte starta");
  }
  if (s_tokens_relay_url != NULL && s_tokens_relay_url[0] != '\0' &&
      xTaskCreate(recovery_task, "tokens-recovery", 3072, NULL, 3,
                  NULL) != pdPASS) {
    ESP_LOGE(TAG, "VibePulse HTTP-vakten kunde inte starta");
  }
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
