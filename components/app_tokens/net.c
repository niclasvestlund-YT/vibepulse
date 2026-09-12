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

#include <inttypes.h>
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
#include "poll_backoff_policy.h"
#include "torget.h"
#include "torget_http.h"

static const char *TAG = "tokens";

#define FETCH_EVERY_MS 30000
/* OBS-13: consecutive misses double the wait, 30 s -> 60 -> 120 -> 240 ->
 * 300 s cap; a success resets. The recovery task's notification still cuts
 * a long wait short, so a station recycle gets its immediate retry. */
#define FETCH_CAP_MS 300000
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

  tk_poll_backoff backoff;
  tk_poll_backoff_init(&backoff, FETCH_EVERY_MS, FETCH_CAP_MS);
  for (;;) {
    /* The recovery wake can arrive before reassociation completes. Wait for
     * live IP here on every pass so the immediate retry is not spent while
     * the station is still disconnected. */
    torget_net_wait();
    tk_tokens t;
    bool fetched = false;
    if (torget_http_get_service("/api/tokens", TK_TOKENS_URL,
                                TK_TOKENS_RELAY_URL,
                                body, sizeof body, &len)
        && tk_tokens_parse(body, len, &t)) {
      fetched = true;
      torget_ui_lock();
      tokens_apply(&t);
      torget_ui_unlock();
      /* OTA-annonsen till plattformen — utanför UI-låset, den rör inget UI
       * själv utan bara tjänstens atomära annonsminne. */
      torget_update_available(
          t.has_ota_available_version ? t.ota_available_version : NULL);
      note_tokens_success();
      /* Utan Claude-källa är volymsiffrorna nollor som inte är mätningar.
       * Loggen säger det i stället för att skriva ut "0.00 Mtok idag",
       * som läses som en dag utan arbete. */
      if (t.volume_failing) {
        ESP_LOGW(TAG,
                 "hämtning ok (volymomräkningen på datorn kraschar — "
                 "värdesidan visar streck, %s; kvoten är live, stale "
                 "claude=%d fable=%d codex=%d)",
                 t.volume_placeholder ? "ingen skanning har lyckats än"
                                      : "räknarna är frysta",
                 t.claude_week.stale, t.claude_model_week.stale,
                 t.codex_week.stale);
      } else if (t.volume_placeholder) {
        /* Första historikskanningen pågår på datorn (issue #62): volymen
         * är en platshållare, inte en mätning — skriv inte ut nollor. */
        ESP_LOGI(TAG,
                 "hämtning ok (volym ej uppmätt än — datorn skannar "
                 "historiken, tidigare värden står kvar; "
                 "stale claude=%d fable=%d codex=%d)",
                 t.claude_week.stale, t.claude_model_week.stale,
                 t.codex_week.stale);
      } else if (t.claude_source_present) {
        ESP_LOGI(TAG,
                 "hämtning ok (%.2f Mtok idag, %d sessioner; "
                 "stale claude=%d fable=%d codex=%d)",
                 t.day_tokens / 1e6, t.day_sessions,
                 t.claude_week.stale, t.claude_model_week.stale,
                 t.codex_week.stale);
      } else {
        ESP_LOGI(TAG,
                 "hämtning ok (ingen Claude-källa på datorn — volym "
                 "okänd, inte noll; stale codex=%d)",
                 t.codex_week.stale);
      }
    } else {
      ESP_LOGW(TAG, "hämtningen avvisad, värden står kvar");
    }
    /* Misslyckad hämtning gör ingenting: appens tick tänder stale efter
     * två minuter — Macen kan ju vara avstängd, det är inte ett fel. Men
     * den jagas inte heller: efter två missar i rad glesnar pollen. */
    uint32_t streak_before = backoff.streak;
    if (tk_poll_backoff_note(&backoff, fetched)) {
      if (fetched) {
        ESP_LOGI(TAG, "tjänsten svarar igen efter %" PRIu32 " missar",
                 streak_before);
      } else {
        ESP_LOGW(TAG, "%" PRIu32 " missar i rad — hämtar var %" PRIu32
                      " s tills tjänsten svarar",
                 backoff.streak, tk_poll_backoff_delay_ms(&backoff) / 1000);
      }
    }

    /* The recovery task can interrupt this sleep after a station recycle.
     * A notification delivered while HTTP is still unwinding is retained and
     * makes the next retry immediate. */
    (void)ulTaskNotifyTake(pdTRUE,
                           pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
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
#define MT_FETCH_CAP_MS 1800000  /* OBS-13: 5 -> 10 -> 20 -> 30 min cap */
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

  tk_poll_backoff backoff;
  tk_poll_backoff_init(&backoff, MT_FETCH_EVERY_MS, MT_FETCH_CAP_MS);
  for (;;) {
    tk_max_tracker t;
    bool fetched = false;
    if (torget_http_get_service("/api/max-tracker", TK_MAX_TRACKER_URL,
                                TK_MAX_TRACKER_RELAY_URL,
                                body, sizeof body, &len)
        && tk_max_tracker_parse(body, len, &t)) {
      fetched = true;
      torget_ui_lock();
      tokens_apply_max_tracker(&t);
      torget_ui_unlock();
      ESP_LOGI(TAG, "max tracker-hämtning ok (stale=%d, streak %d dagar)",
               t.stale, t.coding_streak_days);
    } else {
      ESP_LOGW(TAG, "max tracker-hämtningen avvisad, värden står kvar");
    }
    if (tk_poll_backoff_note(&backoff, fetched) && !fetched) {
      ESP_LOGW(TAG, "max tracker: %" PRIu32 " missar i rad — hämtar var %"
                    PRIu32 " s", backoff.streak,
               tk_poll_backoff_delay_ms(&backoff) / 1000);
    }
    /* Misslyckad hämtning gör ingenting: skärmens egen tick tänder stale
     * efter två minuter — Macen kan ju vara avstängd, det är inte ett fel. */

    vTaskDelay(pdMS_TO_TICKS(tk_poll_backoff_delay_ms(&backoff)));
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
  if (tk_labs_active(TK_LABS_TRACKER))
    xTaskCreate(max_tracker_task, "max-tracker", 6144, NULL, 5, NULL);
#else
  ESP_LOGW(TAG,
           "TK_MAX_TRACKER_URL saknas i secrets.h — Max Tracker visar streck");
#endif
}
