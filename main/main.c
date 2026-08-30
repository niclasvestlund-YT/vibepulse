/*
 * Torgets värdlager på riktig hårdvara: panel, WiFi, tid, ljusramp, rotation
 * och plattforms-API:t apparna står på. Här finns INGEN appdata och INGET
 * hämtande — nätverk bor i apparna (P25-krav 2). Det här är ESP-
 * motsvarigheten till sim/main.c: plattforms-UI:t och apparna är
 * byte-identiska mellan de två världarna, bara värdlagret skiljer.
 *
 * Trådmodell: BSP:n äger LVGL-tasken. Allt som rör UI:t eller delas med en
 * apptask sker under torget_ui_lock() — det är LVGL:s egen mutex, så det
 * behövs inte en till.
 */
#include <stdatomic.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_app_desc.h"
#include "esp_attr.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "esp_heap_caps.h"

#include "bsp/esp-bsp.h"
#include "bsp/touch.h"
#include "driver/gpio.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lv_adapter.h"
#include "lvgl.h"

#include "boot_health.h"
#include "boot_screen.h"
#include "button_policy.h"
#include "agent_monitor.h"
#include "needs_you_net.h"
#include "ota_service.h"
#include "ota_ui.h"
#include "rotation.h"
#include "secrets.h"
#include "torget.h"
#include "vibepulse_recovery.h"
#include "wifi_creds.h"
#include "wifi_setup.h"
#include "wifi_setup_ui.h"
#include "wifi_signal_state.h"
#include "wifi_slots.h"

static const char *TAG = "torget";

#define TICK_EVERY_MS 100 /* ~10 Hz: ljusrampen är mjuk, CPU:n sover */
#define DISPLAY_FLUSH_ROWS 12

/* Nattläge: AMOLED tål mörker bäst av allt, och skärmen står i ett hem.
 * Aktivitet är villkoret, inte klockan: apparna rapporterar liv via
 * torget_keep_awake() (Solelkollen när solen producerar, Tokenmätaren när
 * tokens brinner). En kvart utan aktivitet rampar ner ljuset; ett tryck på
 * den dimmade skärmen väcker den i 30 s (indev-pollning i tick_cb, inga
 * event-krokar).
 *
 * Ljuset RAMPAS, aldrig hoppar: uppåt snabbt (~1,3 s, samma ramp ger
 * boot-fade från svart), nedåt lat (~8 s skymning). */
#define NIGHT_AFTER_US   (15LL * 60LL * 1000000LL)
#define WAKE_HOLD_US     (30LL * 1000000LL)
#define BRIGHT_DAY       100
#define BRIGHT_NIGHT     20
#define BRIGHT_STEP_UP   8   /* per 100 ms-tick: 0→100 på 1,3 s */
#define BRIGHT_STEP_DOWN 1   /* per 100 ms-tick: 100→20 på 8 s */

/* Delat tillstånd. Skrivs av apptaskarna (via torget_keep_awake under
 * UI-låset), läses av LVGL-tasken. */
static int64_t s_last_activity_us;
static int64_t s_last_touch_us;
static int     s_brightness;         /* faktisk nivå just nu, rampad av tick_cb */
static int     s_bright_target = -1; /* mål; loggas bara när det byter */
static lv_indev_t *s_touch;

static EventGroupHandle_t s_net_events;
#define WIFI_GOT_IP BIT0
#define NET_READY   BIT1 /* IP + SNTP: TLS kräver rimlig tid */
/* Declared with the platform state because the public recovery hook reads it
 * before the Wi-Fi implementation section below. */
static atomic_bool s_sta_paused;

/* RTC no-init memory survives esp_restart(). The complemented marker makes a
 * random power-on value fail closed. app_main also requires ESP_RST_SW before
 * exposing it, so brownouts and ordinary power cycles can never masquerade
 * as the bounded VibePulse recovery. */
#define TG_HTTP_RECOVERY_MAGIC 0x56505231u
RTC_NOINIT_ATTR static uint32_t s_http_recovery_magic;
RTC_NOINIT_ATTR static uint32_t s_http_recovery_magic_inverse;
static bool s_http_recovery_booted;

/* Lock-free presentation input only. The network task owns radio sampling;
 * LVGL merely reads the last 0..3 value through torget_wifi_signal_bars(). */
static tg_wifi_signal_state s_wifi_signal = TG_WIFI_SIGNAL_STATE_INIT;

/* ------------------------------------------------- plattforms-API:t (torget.h) */

/*
 * Låsning: ALDRIG bsp_display_lock(). BSP:ns wrapper trycker adapterns
 * esp_err_t genom bool utan invertering — ESP_OK (0) blir false och
 * ESP_ERR_TIMEOUT (0x107) blir true, så sanningsvärdet är SPEGELVÄNT.
 * Upptäckt 2026-08-06 via gdb över USB-JTAG: `if (bsp_display_lock(0))`
 * byggde UI:t exakt när låset INTE togs, parallellt med adapterns
 * lvgl-task, och den olåsta heapen (LV_OS_NONE) korrumperades så att BÅDA
 * taskarna fastnade i eviga loopar. Detaljer: spec/hardware.md.
 */
void torget_ui_lock(void)   { ESP_ERROR_CHECK(esp_lv_adapter_lock(-1)); }
void torget_ui_unlock(void) { esp_lv_adapter_unlock(); }
bool torget_ui_try_lock(uint32_t timeout_ms) {
  return esp_lv_adapter_lock((int32_t)timeout_ms) == ESP_OK;
}

int64_t torget_now_us(void) { return esp_timer_get_time(); }

void torget_net_wait(void) {
  /* NET_READY is the one-time clock gate; WIFI_GOT_IP is the live station
   * gate. Keeping both here lets an app task safely call this again after a
   * recovery disconnect instead of racing its retry against reassociation. */
  xEventGroupWaitBits(s_net_events, NET_READY | WIFI_GOT_IP,
                      pdFALSE, pdTRUE, portMAX_DELAY);
}

bool torget_net_recover_http_stall(void) {
  if (atomic_load(&s_sta_paused) ||
      (xEventGroupGetBits(s_net_events) & WIFI_GOT_IP) == 0) {
    return false;
  }
  esp_err_t err = esp_wifi_disconnect();
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "HTTP-vakten kunde inte återställa WiFi: %s",
             esp_err_to_name(err));
    return false;
  }
  return true;
}

void torget_net_restart_http_stall(void) {
  if (atomic_load(&s_sta_paused) ||
      (xEventGroupGetBits(s_net_events) & WIFI_GOT_IP) == 0) {
    return;
  }
  ESP_LOGE(TAG, "HTTP-vakten eskalerar till kontrollerad omstart");
  s_http_recovery_magic = TG_HTTP_RECOVERY_MAGIC;
  s_http_recovery_magic_inverse = ~TG_HTTP_RECOVERY_MAGIC;
  esp_restart();
}

bool torget_net_http_stall_recovery_booted(void) {
  return s_http_recovery_booted;
}

uint8_t torget_wifi_signal_bars(void) {
  return tg_wifi_signal_bars(&s_wifi_signal);
}

void torget_keep_awake(void) { s_last_activity_us = esp_timer_get_time(); }

void torget_update_available(const char *version) {
  torget_ota_service_update_available(version);
}

/* Bootskärmens datasignal: första lyckade hämtningen tar ner skärmen.
 * Atomär flagga — tokens_apply kallar under UI-låset och stage() tar
 * låset självt, så själva nedtagningen skjuts till nästa tick. */
static _Atomic bool s_data_alive;
void torget_data_alive(void) { atomic_store(&s_data_alive, true); }

/* ------------------------------------------------------------------- wifi */

/* Bootdiagnostik, medvetet permanent: skanna EN gång från nättasken (inte
 * event-loopen — dess stack sväljer varken blockering eller AP-listan)
 * innan första connect. S3:an är 2,4 GHz-only, så ett nät som saknas i
 * listan finns inte i dess värld oavsett vad telefonen ser. 2,5 s extra vid
 * boot är priset; sedan setupfönstret finns (components/torget_wifi) står
 * samma sanning också på glaset, inte bara i en logg ingen läser. */
static void scan_debug(const char *target) {
  static wifi_ap_record_t ap[20]; /* 1,6 kB — på .bss, inte på stacken */
  ESP_LOGI(TAG, "skannar 2,4 GHz-banden...");
  if (esp_wifi_scan_start(NULL, true) != ESP_OK) {
    ESP_LOGW(TAG, "skanningen gick inte att starta");
    return;
  }
  uint16_t n = 20;
  if (esp_wifi_scan_get_ap_records(&n, ap) == ESP_OK) {
    for (int i = 0; i < n; i++)
      ESP_LOGI(TAG, "  ser: \"%s\" kanal %d, %d dBm, auth %d",
               (const char *)ap[i].ssid, ap[i].primary, ap[i].rssi, ap[i].authmode);
    ESP_LOGI(TAG, "  (%d nät totalt; vårt mål: \"%s\")", n, target);
  }
}

static bool s_first_start = true;

/* Reservnätet (telefonens internetdelning — beslut 2026-08-07) är valfritt:
 * gamla secrets.h utan TG_WIFI2_* bygger oförändrat via tomma defaultar. */
#ifndef TG_WIFI2_SSID
#define TG_WIFI2_SSID ""
#define TG_WIFI2_PASS ""
#endif

/*
 * Kandidatlistan: näten panelen minns (NVS, components/torget_wifi) först —
 * det som senast fungerade överst — och secrets.h sist som en OFÖRÄNDERLIG
 * botten. Botten är hela poängen: setupfönstret kan lägga till platser men
 * aldrig ta bort hemnätet, så ingen felkonfiguration kan göra panelen
 * ounderhållbar och kräva en USB-flashning för att komma tillbaka.
 *
 * Listan skrivs av setupvakten (efter nya uppgifter) och läses av
 * event-loopen — därför ett kort mutexhåll runt varje åtkomst.
 */
static tg_wifi_slot s_cand[TG_WIFI_SLOTS + 2];
static int s_cand_n;
static int s_cand_i;      /* vilket nät vi jagar just nu */
static int s_cand_misses; /* missar i rad på DET nätet   */
static SemaphoreHandle_t s_cand_lock;
static tg_wifi_slot s_trial;
static bool s_trial_active; /* skyddas av s_cand_lock */

/* Medan setupfönstret äger radion (skanning + accesspunkt) ska
 * event-handlern INTE ropa esp_wifi_connect: en connect mitt i en skanning
 * ger bara ESP_ERR_WIFI_STATE och ett brus av misslyckanden i loggen. */
static atomic_bool s_trial_ignore_disconnect;
static _Atomic int s_disconnect_reason;

/* Senaste frånkopplingsorsaken i klartext, för den ärliga nätsidan. */
static char s_reason_text[40];

static void cand_lock(void)   { xSemaphoreTake(s_cand_lock, portMAX_DELAY); }
static void cand_unlock(void) { xSemaphoreGive(s_cand_lock); }

/* Bygger om listan ur NVS + secrets.h. prefer != NULL ställer jakten direkt
 * på det nätet (setupfönstret har just fått dess lösenord). */
static void wifi_reload_candidates(const char *prefer) {
  static tg_wifi_slot remembered[TG_WIFI_SLOTS];
  static tg_wifi_slot fixed[2];
  const tg_wifi_slot *order[TG_WIFI_SLOTS + 2];

  tg_wifi_creds_load(remembered);

  memset(fixed, 0, sizeof fixed);
  snprintf(fixed[0].ssid, sizeof fixed[0].ssid, "%s", TG_WIFI_SSID);
  snprintf(fixed[0].pass, sizeof fixed[0].pass, "%s", TG_WIFI_PASS);
  snprintf(fixed[1].ssid, sizeof fixed[1].ssid, "%s", TG_WIFI2_SSID);
  snprintf(fixed[1].pass, sizeof fixed[1].pass, "%s", TG_WIFI2_PASS);

  int n = tg_wifi_candidates(remembered, TG_WIFI_SLOTS, fixed, 2, order,
                             TG_WIFI_SLOTS + 2);

  cand_lock();
  s_cand_n = n;
  for (int i = 0; i < n; i++) s_cand[i] = *order[i];
  s_cand_i = 0;
  s_cand_misses = 0;
  if (prefer) {
    for (int i = 0; i < n; i++)
      if (strcmp(s_cand[i].ssid, prefer) == 0) { s_cand_i = i; break; }
  }
  cand_unlock();

  ESP_LOGI(TAG, "%d nät i jaktlistan (%d ihågkomna + secrets.h)", n,
           tg_wifi_creds_count());
}

/* Nätet vi jagar just nu, kopierat till anroparens egen buffert så listan
 * aldrig läses utan lås. */
static void wifi_copy_current_ssid(char *out, size_t cap) {
  if (!cap) return;
  cand_lock();
  if (s_trial_active) snprintf(out, cap, "%s", s_trial.ssid);
  else if (s_cand_n > 0) snprintf(out, cap, "%s", s_cand[s_cand_i].ssid);
  else out[0] = '\0';
  cand_unlock();
}

/* Krokens variant. Bufferten är statisk och därför BARA för setupvakten —
 * den är hookens enda anropare. Event-loopen och nättasken tar egna lokala
 * buffertar; två taskar som skriver samma static hade kunnat rita ett
 * hopblandat nätnamn på glaset just när någon felsöker sitt nät. */
static const char *wifi_current_ssid(void) {
  static char ssid[TG_WIFI_SSID_CAP];
  wifi_copy_current_ssid(ssid, sizeof ssid);
  return ssid;
}

static const char *wifi_last_reason(void) {
  return s_reason_text[0] ? s_reason_text : NULL;
}

static int last_disconnect_reason(void) {
  return atomic_load(&s_disconnect_reason);
}

static esp_err_t wifi_apply_current(void) {
  wifi_config_t cfg = { 0 };
  cand_lock();
  if (s_trial_active) {
    strlcpy((char *)cfg.sta.ssid, s_trial.ssid, sizeof cfg.sta.ssid);
    strlcpy((char *)cfg.sta.password, s_trial.pass, sizeof cfg.sta.password);
  } else if (s_cand_n > 0) {
    strlcpy((char *)cfg.sta.ssid, s_cand[s_cand_i].ssid, sizeof cfg.sta.ssid);
    strlcpy((char *)cfg.sta.password, s_cand[s_cand_i].pass,
            sizeof cfg.sta.password);
  }
  cand_unlock();
  /* Tröskeln följer nätet, inte en global gissning: ett öppet café-nät har
   * ingen PSK och skulle avvisas tyst av en fast WPA2-tröskel — det var
   * exakt vad den gamla koden gjorde på resa. */
  cfg.sta.threshold.authmode =
      cfg.sta.password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
  return esp_wifi_set_config(WIFI_IF_STA, &cfg);
}

static void wifi_note_reason(int reason) {
  /* Orsakskoden är diagnosen: 201 = nätet syns inte alls (fel namn, eller
   * bara 5 GHz — S3:an hör enbart 2,4 GHz), 15/204 = fel lösenord. Samma
   * sanning som loggen burit sedan 2026-08-06, nu också på glaset. */
  switch (reason) {
    case 201:
      snprintf(s_reason_text, sizeof s_reason_text, "NOT SEEN - 2.4 GHZ ONLY");
      break;
    case 15:
    case 204:
      snprintf(s_reason_text, sizeof s_reason_text, "WRONG PASSWORD");
      break;
    default:
      snprintf(s_reason_text, sizeof s_reason_text, "RADIO REASON %d", reason);
      break;
  }
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
  (void)arg;
  if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
    if (s_first_start) { s_first_start = false; return; } /* nättasken sköter första */
    if (!atomic_load(&s_sta_paused)) esp_wifi_connect();
  } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
    xEventGroupClearBits(s_net_events, WIFI_GOT_IP);
    tg_wifi_signal_event(&s_wifi_signal, 0);
    int reason = ((wifi_event_sta_disconnected_t *)data)->reason;
    bool trial_transition = atomic_exchange(&s_trial_ignore_disconnect, false);
    if (!trial_transition) {
      atomic_store(&s_disconnect_reason, reason);
      wifi_note_reason(reason);
    }
    char ssid[TG_WIFI_SSID_CAP];
    wifi_copy_current_ssid(ssid, sizeof ssid);
    ESP_LOGW(TAG, "WiFi tappat (\"%s\", orsak %d), återansluter", ssid, reason);
    /* Setupfönstret äger radion: ingen återanslutning förrän det släppt. */
    if (atomic_load(&s_sta_paused)) return;
    /* Växelbruk över hela listan: efter fyra missar i rad provas nästa
     * kandidat. Ingen prioritet — det som svarar vinner, och tappas det
     * börjar jakten om från samma plats i listan. */
    bool switched = false;
    bool trial_active = false;
    cand_lock();
    trial_active = s_trial_active;
    if (!trial_active && s_cand_n > 1 &&
        tg_wifi_should_switch(++s_cand_misses)) {
      s_cand_i = (s_cand_i + 1) % s_cand_n;
      s_cand_misses = 0;
      switched = true;
    }
    cand_unlock();
    if (switched) {
      wifi_copy_current_ssid(ssid, sizeof ssid);
      ESP_LOGI(TAG, "provar \"%s\" i stället", ssid);
      esp_err_t err = wifi_apply_current();
      if (err != ESP_OK)
        ESP_LOGW(TAG, "kunde inte byta WiFi-konfiguration: %s",
                 esp_err_to_name(err));
    }
    vTaskDelay(pdMS_TO_TICKS(2000));
    if (!atomic_load(&s_sta_paused)) esp_wifi_connect();
  } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
    /* GOT_IP is enough to say connected even before the first RSSI sample. */
    xEventGroupSetBits(s_net_events, WIFI_GOT_IP);
    tg_wifi_signal_event(&s_wifi_signal, 1);
    char ssid[TG_WIFI_SSID_CAP];
    wifi_copy_current_ssid(ssid, sizeof ssid);
    ESP_LOGI(TAG, "WiFi uppe (\"%s\")", ssid);
    torget_boot_screen_stage(TG_BOOT_WIFI_UP);
    s_reason_text[0] = '\0';
    atomic_store(&s_disconnect_reason, 0);
    cand_lock();
    s_cand_misses = 0;
    cand_unlock();
    /* INGEN NVS här. Att flytta upp nätet i listan läser och skriver en
     * blob på 600 byte — dels blockerar en flashskrivning event-loopen
     * (cachen suspenderas), dels ryms arrayen illa på dess lilla stack.
     * Nätvakten gör det i stället, på sin egen. */
  }
}

/* Setupfönstrets krokar in i värdlagret. Fönstret äger aldrig radion
 * själv — det ber om pausen och lämnar tillbaka den. */
static bool hook_have_ip(void) {
  return (xEventGroupGetBits(s_net_events) & WIFI_GOT_IP) != 0;
}

static void hook_sta_pause(bool paused) {
  atomic_store(&s_sta_paused, paused);
  if (!paused) esp_wifi_connect();
}

static bool hook_try_credentials(const char *ssid, const char *password) {
  if (!tg_wifi_ssid_valid(ssid) || !tg_wifi_pass_valid(password)) return false;

  cand_lock();
  memset(&s_trial, 0, sizeof s_trial);
  strlcpy(s_trial.ssid, ssid, sizeof s_trial.ssid);
  strlcpy(s_trial.pass, password, sizeof s_trial.pass);
  s_trial_active = true;
  cand_unlock();

  atomic_store(&s_disconnect_reason, 0);
  atomic_store(&s_sta_paused, true);
  xEventGroupClearBits(s_net_events, WIFI_GOT_IP);
  esp_err_t disconnect_err = esp_wifi_disconnect();
  atomic_store(&s_trial_ignore_disconnect, disconnect_err == ESP_OK);
  esp_err_t err = wifi_apply_current();
  atomic_store(&s_sta_paused, false);
  if (err == ESP_OK) err = esp_wifi_connect();
  if (err == ESP_OK) return true;

  ESP_LOGW(TAG, "kunde inte starta WiFi-försöket: %s", esp_err_to_name(err));
  cand_lock();
  memset(&s_trial, 0, sizeof s_trial);
  s_trial_active = false;
  cand_unlock();
  wifi_reload_candidates(NULL);
  (void)wifi_apply_current();
  return false;
}

static void hook_credentials_accepted(const char *ssid) {
  cand_lock();
  memset(&s_trial, 0, sizeof s_trial);
  s_trial_active = false;
  cand_unlock();
  wifi_reload_candidates(ssid);
}

static void hook_credentials_abandoned(void) {
  cand_lock();
  bool had_trial = s_trial_active;
  memset(&s_trial, 0, sizeof s_trial);
  s_trial_active = false;
  cand_unlock();
  if (!had_trial) return;
  atomic_store(&s_disconnect_reason, 0);
  wifi_reload_candidates(NULL);
  esp_err_t err = wifi_apply_current();
  if (err != ESP_OK)
    ESP_LOGW(TAG, "kunde inte återställa sparat WiFi: %s",
             esp_err_to_name(err));
}

/* Kallas av nätvakten när en uppkoppling just lyckats — den har stacken
 * och friheten att röra flashen som event-loopen saknar. Nätet som gav IP
 * flyter upp i listan så det provas först nästa gång. No-op för
 * secrets.h-botten: den bor aldrig i NVS. */
static void hook_ip_acquired(void) {
  char ssid[TG_WIFI_SSID_CAP];
  wifi_copy_current_ssid(ssid, sizeof ssid);
  if (ssid[0]) tg_wifi_creds_mark_ok(ssid);
}

static const tg_wifi_setup_hooks s_setup_hooks = {
  /* Flushens DMA-behov: golvet setupfönstrets grindar mäter mot —
   * samma tal som heap-larmet i tick_cb vaktar. */
  .flush_dma_bytes = (size_t)DISPLAY_FLUSH_ROWS * 480u * 2u,
  .have_ip = hook_have_ip,
  .ip_acquired = hook_ip_acquired,
  .sta_pause = hook_sta_pause,
  .try_credentials = hook_try_credentials,
  .credentials_accepted = hook_credentials_accepted,
  .credentials_abandoned = hook_credentials_abandoned,
  .last_disconnect_reason = last_disconnect_reason,
  .current_ssid = wifi_current_ssid,
  .last_reason = wifi_last_reason,
};

static void wifi_start(void) {
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  esp_netif_create_default_wifi_sta();

  wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&init));
  ESP_ERROR_CHECK(esp_event_handler_instance_register(
    WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL, NULL));
  ESP_ERROR_CHECK(esp_event_handler_instance_register(
    IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL, NULL));

  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  wifi_reload_candidates(NULL); /* NVS överst, secrets.h som botten */
  ESP_ERROR_CHECK(wifi_apply_current());
  ESP_ERROR_CHECK(esp_wifi_start());
  /* Torget is a wall-powered live display, not a battery sensor. ESP-IDF's
   * default MIN_MODEM sleep trades receive latency and radio continuity for
   * power that this product does not need to save. Keep the station awake so
   * the one-second agent path and five-second encrypted mailbox remain
   * deterministic. A failure here is diagnosable but not a boot brick. */
  esp_err_t ps_err = esp_wifi_set_ps(WIFI_PS_NONE);
  if (ps_err != ESP_OK) {
    ESP_LOGW(TAG, "kunde inte stänga av WiFi modem-sömn: %s",
             esp_err_to_name(ps_err));
  }
}

/* TLS kräver en rimlig klocka: utan tid är serverns certifikat "ännu inte
 * giltigt" och varje HTTPS-hämtning faller. Kortets RTC är inte batteri-
 * backad, så SNTP är förutsättningen för NET_READY. */
static void time_sync(void) {
  esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
  ESP_ERROR_CHECK(esp_netif_sntp_init(&cfg));
  if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(20000)) != ESP_OK)
    ESP_LOGW(TAG, "ingen tid från SNTP ännu, apparnas hämtningar får vänta på den");
  else
    ESP_LOGI(TAG, "tid synkad");
}

/* Plattformens nättask: koppla upp, synka tid, släpp fram apparna
 * (torget_net_wait), försvinn. Apparna äger allt hämtande därefter. */
static void net_task(void *arg) {
  (void)arg;
  vTaskDelay(pdMS_TO_TICKS(500)); /* låt STA-läget starta klart */
  char target[TG_WIFI_SSID_CAP];
  wifi_copy_current_ssid(target, sizeof target);
  scan_debug(target);
  esp_wifi_connect();
  xEventGroupWaitBits(s_net_events, WIFI_GOT_IP, pdFALSE, pdTRUE, portMAX_DELAY);
  time_sync();
  torget_boot_screen_stage(TG_BOOT_TIME_OK);
  xEventGroupSetBits(s_net_events, NET_READY);
  vTaskDelete(NULL);
}

/* RSSI is sampled away from the LVGL task.  Five seconds is deliberate:
 * signal strength is orientation context, not a live transport-health meter,
 * and a low-priority periodic query must never contend with display work. */
static void wifi_signal_task(void *arg) {
  (void)arg;
  for (;;) {
    unsigned sampled = tg_wifi_signal_sample_begin(&s_wifi_signal);
    wifi_ap_record_t ap;
    uint8_t bars = 0;
    if ((xEventGroupGetBits(s_net_events) & WIFI_GOT_IP) != 0 &&
        esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
      bars = ap.rssi >= -55 ? 3 : ap.rssi >= -70 ? 2 : 1;
    }
    (void)tg_wifi_signal_sample_commit(&s_wifi_signal, sampled, bars);
    vTaskDelay(pdMS_TO_TICKS(5000));
  }
}

/* ------------------------------------------------------- LVGL-tasken, 10 Hz */

static void tick_cb(lv_timer_t *t) {
  (void)t;
  int64_t now = esp_timer_get_time();

  /* Schemaläggarbeviset till OTA-hälsogrinden: första ticken bevisar att
   * LVGL-tasken faktiskt snurrar under verklig bootlast — inte bara att
   * timern skapades. Atomär markering, inga lås. */
  static bool scheduler_marked;
  if (!scheduler_marked) {
    scheduler_marked = true;
    torget_boot_health_mark(TG_HEALTH_SCHEDULER);
  }

  /* Minnestelemetri var 10:e sekund: SPI-flushen till panelen behöver
   * DMA-dugligt internminne, och tar det slut fastnar hela ritpipen i
   * NO_MEM (sett vid första flashen 2026-08-06: TLS-hämtning + omritning
   * sammanföll och panelen tystnade permanent). Largest block är siffran
   * som avgör — fragmentering syns inte i totalsumman. */
  static int heap_probe;
  if (++heap_probe >= 100) {
    heap_probe = 0;
    unsigned dma_largest = (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA);
    ESP_LOGI(TAG, "heap: internt %u fritt (största block %u, lägsta någonsin %u), DMA största %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             dma_largest);
    tk_agent_render_stats render_stats;
    tk_agent_monitor_render_stats(&render_stats);
    ESP_LOGI(TAG, "needs-you render: full=%u ring=%u unchanged=%u",
             (unsigned)render_stats.full_repaints,
             (unsigned)render_stats.ring_updates,
             (unsigned)render_stats.unchanged_ticks);
    tk_agent_monitor_render_stats_reset();
    /* Tidig varning INNAN glaset fryser: panelflushen behöver ett
     * sammanhängande DMA-block på DISPLAY_FLUSH_ROWS×480×2 byte. Faller
     * största DMA-blocket mot det taket dör nästa flush i NO_MEM och hela
     * ritpipen fastnar tyst (frysjakten 2026-08-16: LVGL:s interna pool
     * svalt blocket → låst render). Larmet gör en framtida regression
     * högljudd i stället för tyst. Marginal ×2 = andrum för TLS/WiFi-spikar. */
    const unsigned flush_dma = (unsigned)DISPLAY_FLUSH_ROWS * 480u * 2u;
    if (dma_largest < flush_dma * 2u)
      ESP_LOGW(TAG, "LÅGT DMA-block: %u byte (flush behöver %u) — nära fryströskeln",
               dma_largest, flush_dma);
  }

  /* Väckning: ett finger på glaset räknas som aktivitet. Pollat, inte
   * event-kopplat — apparnas UI:n får sluka gesterna bäst de vill,
   * indev-tillståndet ser trycket ändå. */
  if (s_touch && lv_indev_get_state(s_touch) == LV_INDEV_STATE_PRESSED)
    s_last_touch_us = now;

  /* KEY3 (GPIO18, aktiv låg): kort tryck = nästa app, tre sekunders håll =
   * OTA-underhållsfönster. Tidsreglerna bor i den värdtestade knappolicyn;
   * 10 Hz-ticken pollar vidare medan knappen är nere så hållet avfyras
   * utan släpp. Körs i LVGL-tasken — därför bara atomära tjänsteanrop här,
   * aldrig torget_ota_ui_set (som tar UI-låset). */
  /* Bootskärmen tas ner av första datalivet — eller ge-upp-taket när
   * nätet aldrig kommer (45 s: två hämtcykler + marginal; bakom står
   * apparnas ärliga NO DATA). Låset är rekursivt, så stage() från
   * LVGL-tasken är säkert. */
  static bool boot_screen_done;
  if (!boot_screen_done) {
    if (atomic_load(&s_data_alive)) {
      boot_screen_done = true;
      torget_boot_screen_stage(TG_BOOT_DATA_OK);
    } else if (now > 45LL * 1000000LL) {
      boot_screen_done = true;
      torget_boot_screen_stage(TG_BOOT_GIVE_UP);
    }
  }

  static tg_button_policy key3;
  bool key3_down = gpio_get_level(GPIO_NUM_18) == 0;
  tg_button_action key3_action = tg_button_update(&key3, key3_down, now);
  if (key3_down)
    s_last_touch_us = now; /* knappkontakt är aktivitet, precis som touch */
  if (torget_wifi_setup_owns_input()) {
    /* Även STARTING äger knappen. AP/skanning kan ta sekunder och under den
     * tiden får det utlösande släppet aldrig bli appväxling eller panik.
     * request_close() ignorerar STARTING men stänger OPEN/JOINING/JOINED/
     * FAILED, så nödutgången finns kvar när fönstret väl kan stängas. */
    if (key3_action == TG_BUTTON_NEXT_APP || key3_action == TG_BUTTON_PANIC)
      torget_wifi_setup_request_close();
  } else if (torget_ota_service_maintenance_open()) {
    /* While the update window owns the glass, ANY release closes it — a
     * short tap or the panic-window hold — so the nödutgången never depends
     * on out-tapping the panic threshold (a real trap found on hardware
     * 2026-08-16: a ~2 s press panicked instead of closing, leaving the
     * window stuck). Ten minutes without an escape once made a healthy
     * device look hung. No panic fires while the window is up.
     *
     * Ett NYTT fullt 3 s-håll byter fönster: OTA-fönstret stängs och
     * setupfönstret öppnar. Det är vägen att nå WIFI SETUP på en panel som
     * HAR nät (håll–håll: först uppdateringsfönstret, sen nätfönstret) —
     * utan den kunde ett nytt nät bara läras ut när panelen redan var
     * strandad. Nödutgången är intakt: varje släpp FÖRE tre sekunder
     * stänger, bara ett medvetet fullbordat håll byter. */
    if (key3_action == TG_BUTTON_NEXT_APP || key3_action == TG_BUTTON_PANIC) {
      torget_ota_service_close_maintenance();
    } else if (key3_action == TG_BUTTON_OPEN_MAINTENANCE) {
      /* Bara begäran härifrån: setupvaktens window_open äger överlämningen
       * av port 80 (stänger OTA-fönstret och väntar ut dess httpd-stopp).
       * Att stänga HÄR hade gjort portbytet till en kapplöpning mellan två
       * vakter som pollar var 500:e ms. */
      torget_wifi_setup_request_open();
    }
  } else if (key3_action == TG_BUTTON_NEXT_APP) {
    torget_app_next();
  } else if (key3_action == TG_BUTTON_PANIC) {
    /* Mellanhåll-och-släpp med stängt fönster: neka allt Needs You parkerat.
     * Bara ett köinlägg (atomärt), säkert från LVGL-tasken; en avstängd
     * svarskanal (ingen enhetsnyckel) gör det till en no-op. */
    tk_needs_you_send_panic();
  } else if (key3_action == TG_BUTTON_OPEN_MAINTENANCE) {
    /* Ett håll betyder det ENDA som kan hjälpa just nu. Utan IP kan ett
     * OTA-fönster aldrig ta emot en uppladdning — då är nätet problemet,
     * och hållet öppnar setupfönstret i stället. Med IP är allt som förut.
     * OTA:s samtyckesmodell är oförändrad: fönstret öppnas fortfarande
     * bara från enheten, aldrig av ett skript. */
    if (hook_have_ip()) torget_ota_service_open_maintenance();
    else torget_wifi_setup_request_open();
  }

  int target = ((now - s_last_activity_us) > NIGHT_AFTER_US
                && (now - s_last_touch_us) > WAKE_HOLD_US)
               ? BRIGHT_NIGHT : BRIGHT_DAY;
  if (target != s_bright_target) {
    s_bright_target = target;
    ESP_LOGI(TAG, "ljusmål: %d %%", target);
  }
  if (s_brightness != target) {
    int step = (target > s_brightness) ? BRIGHT_STEP_UP : -BRIGHT_STEP_DOWN;
    s_brightness += step;
    /* kliv aldrig förbi målet, i någondera riktningen */
    if ((step > 0 && s_brightness > target) || (step < 0 && s_brightness < target))
      s_brightness = target;
    bsp_display_brightness_set(s_brightness);
  }
}

/* ------------------------------------------------------------- displayen */

/*
 * Egen displaystart i stället för bsp_display_start_with_config: BSP:n
 * hårdkodar buffer_height 50, och en 480×50×2-flush är 48 KB som SPI-
 * drivrutinen måste bounce-kopiera till ett sammanhängande DMA-block
 * (ritbuffertarna bor i PSRAM). Torgets interna heap har ~30 KB som
 * största block — varje stor flush dog i NO_MEM och skärmen frös (hittat
 * via heap-telemetrin 2026-08-06: 81 KB fritt men största block 31 KB).
 * 24 rader fungerade normalt men kunde ändå tappa SPI-kön när TLS
 * fragmenterade internminnet. Tolv rader är 11 520 byte: tre köade
 * transaktioner ryms även under samtidiga HTTPS-hämtningar.
 *
 * Allt nedan är exakt BSP:ns bsp_display_lcd_init/indev_init med publika
 * API:er — enda avvikelserna är bufferthöjden och 16 KB LVGL-stack.
 */
static esp_lcd_panel_handle_t s_panel;
static esp_lcd_panel_io_handle_t s_panel_io;

/* 2-pixel-alignment (spec/hardware.md): dirty-areor rundas till jämn start
 * och udda slut i båda axlarna, annars pixelskräp. Kopia av BSP:ns rounder. */
static void rounder_event_cb(lv_event_t *e) {
  lv_area_t *area = (lv_area_t *)lv_event_get_param(e);
  area->x1 = (area->x1 >> 1) << 1;
  area->y1 = (area->y1 >> 1) << 1;
  area->x2 = ((area->x2 >> 1) << 1) + 1;
  area->y2 = ((area->y2 >> 1) << 1) + 1;
}

static void display_start(void) {
  esp_lv_adapter_config_t adapter_cfg = ESP_LV_ADAPTER_DEFAULT_CONFIG();
  /* 16 KB: appröttarna gör trädet djupare än defaultens 8 KB klarade. */
  adapter_cfg.task_stack_size = 16 * 1024;
  ESP_ERROR_CHECK(esp_lv_adapter_init(&adapter_cfg));

  const bsp_display_config_t disp_config = {
    .max_transfer_sz = BSP_LCD_H_RES * DISPLAY_FLUSH_ROWS *
                       BSP_LCD_BITS_PER_PIXEL / 8,
  };
  ESP_ERROR_CHECK(bsp_display_new(&disp_config, &s_panel, &s_panel_io));

  esp_lv_adapter_display_config_t disp_cfg = {
    .panel = s_panel,
    .panel_io = s_panel_io,
    .profile = {
      .interface = ESP_LV_ADAPTER_PANEL_IF_OTHER,
      .rotation = ESP_LV_ADAPTER_ROTATE_0,
      .hor_res = BSP_LCD_H_RES,
      .ver_res = BSP_LCD_V_RES,
      .buffer_height = DISPLAY_FLUSH_ROWS,
      .use_psram = true,
      .enable_ppa_accel = false,
      .require_double_buffer = true,
    },
    .tear_avoid_mode = ESP_LV_ADAPTER_TEAR_AVOID_MODE_NONE,
  };
  lv_display_t *disp = esp_lv_adapter_register_display(&disp_cfg);
  assert(disp);
  lv_display_add_event_cb(disp, rounder_event_cb, LV_EVENT_INVALIDATE_AREA, NULL);

  ESP_ERROR_CHECK(bsp_display_brightness_init());

  /* Touchparet hör ihop med MADCTL 0xA0 — ändra aldrig ena sidan ensam. */
  bsp_display_cfg_t touch_cfg = {
    .touch_flags = { .swap_xy = 1, .mirror_x = 0, .mirror_y = 1 },
  };
  esp_lcd_touch_handle_t tp = NULL;
  ESP_ERROR_CHECK(bsp_touch_new(&touch_cfg, &tp));
  esp_lv_adapter_touch_config_t adapter_touch =
    ESP_LV_ADAPTER_TOUCH_DEFAULT_CONFIG(disp, tp);
  s_touch = esp_lv_adapter_register_touch(&adapter_touch);

  ESP_ERROR_CHECK(esp_lv_adapter_start());
}

/* MADCTL-vridningen ur BSP:ns bsp_display_rotation_set — den läser BSP:ns
 * statiska handles som aldrig sätts när vi startar displayen själva.
 *
 * PLUS panelens gap: CO5300-glasets fönster börjar på kontrollerkolumn 6
 * (initsekvensens CASET är 0x0006..0x01DD) och drivrutinen adderar
 * x_gap/y_gap till varje adressfönster — men BSP:n justerar aldrig gapet
 * vid rotation. I bootläget 0xA0 går mappningen jämnt ut; i andra lägen
 * hamnar 6-pixelremsan oskriven vid en kant (den vita linjen, hittad på
 * foto 2026-08-06 i läge 0xC0). Konstanterna nedan kalibreras med P24-
 * metoden: ETT strukturerat fyrlägestest, en konstant per läge — aldrig
 * fotoforensik. Ser du en ljus kantlinje i ett läge: justera det lägets
 * par (6 på den axel linjen sitter, spegelvänt om den flyttar till
 * motsatt kant). */
esp_err_t torget_display_rotation_set(bsp_display_rotation_t rotation) {
  static const uint8_t MADCTL[4] = { 0x00, 0x60, 0xC0, 0xA0 };
  static const int GAP[4][2] = { /* {x_gap, y_gap} per läge */
    {0, 0},  /* 0x00 */
    {6, 0},  /* 0x60 */
    {0, 6},  /* 0xC0 — linjen satt i botten: skjut raderna +6 */
    {0, 0},  /* 0xA0 — bootläget, verifierat rent */
  };
  if (rotation > BSP_DISPLAY_ROTATE_270) return ESP_ERR_INVALID_ARG;
  uint32_t lcd_cmd = (0x36 << 8) | (0x02 << 24);
  ESP_LOGI(TAG, "MADCTL 0x%02X gap %d/%d (läge %d)", MADCTL[rotation],
           GAP[rotation][0], GAP[rotation][1], rotation);
  esp_lcd_panel_set_gap(s_panel, GAP[rotation][0], GAP[rotation][1]);
  return esp_lcd_panel_io_tx_param(s_panel_io, lcd_cmd, &MADCTL[rotation], 1);
}

/* ------------------------------------------------------------------- start */

static const char *reset_reason_name(esp_reset_reason_t r) {
  switch (r) {
  case ESP_RST_POWERON: return "strömpåslag";
  case ESP_RST_SW: return "mjukvaruomstart";
  case ESP_RST_PANIC: return "PANIK";
  case ESP_RST_INT_WDT: return "AVBROTTSVAKTHUND";
  case ESP_RST_TASK_WDT: return "TASKVAKTHUND";
  case ESP_RST_WDT: return "annan vakthund";
  case ESP_RST_BROWNOUT: return "BROWNOUT — misstänk strömförsörjningen";
  case ESP_RST_DEEPSLEEP: return "djupsömn";
  case ESP_RST_EXT: return "extern reset";
  case ESP_RST_USB: return "USB-reset";
  case ESP_RST_JTAG: return "JTAG";
  case ESP_RST_SDIO: return "SDIO";
  case ESP_RST_EFUSE: return "efuse-fel";
  case ESP_RST_PWR_GLITCH: return "spänningsglitch";
  case ESP_RST_CPU_LOCKUP: return "CPU-låsning";
  default: return "okänd";
  }
}

void app_main(void) {
  /* Bootbanderollen svarar på två frågor loggen annars inte kan:
   * "kör kortet det jag just flashade?" (versionen är git describe via
   * ESP-IDF:s appdeskriptor, plus byggtiden) och "varför startade det om?"
   * (orsaken — en nattlig panik/brownout är annars osynlig i efterhand).
   * Serverns motsvarighet är rev/startedAt på GET /. */
  const esp_app_desc_t *app = esp_app_get_description();
  esp_reset_reason_t rr = esp_reset_reason();
  s_http_recovery_booted =
      rr == ESP_RST_SW &&
      s_http_recovery_magic == TG_HTTP_RECOVERY_MAGIC &&
      s_http_recovery_magic_inverse == ~TG_HTTP_RECOVERY_MAGIC;
  s_http_recovery_magic = 0;
  s_http_recovery_magic_inverse = 0;
  ESP_LOGI(TAG, "boot: %s %s (byggd %s %s, IDF %s), omstartsorsak %s (%d)",
           app->project_name, app->version, app->date, app->time,
           app->idf_ver, reset_reason_name(rr), (int)rr);
  if (s_http_recovery_booted) {
    ESP_LOGW(TAG, "startup-health: föregående VibePulse HTTP-stall "
                  "eskalerade till kontrollerad omstart");
  }

  esp_err_t nvs = nvs_flash_init();
  if (nvs == ESP_ERR_NVS_NO_FREE_PAGES || nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    nvs = nvs_flash_init();
  }
  ESP_ERROR_CHECK(nvs);

  /* OTA-hälsogrinden direkt efter NVS: är detta första boot på en ny
   * avbild börjar 8/15-sekundersklockan ticka HÄR, och bevisen markeras
   * allteftersom bootordningen nedan levererar dem. En stabil boot gör
   * anropet till en ren bevisinspektion. */
  torget_boot_health_start();

  /* Eventgruppen FÖRE UI-bygget: apparnas hämttasker startar i create()
   * och blockerar direkt i torget_net_wait() — fanns gruppen inte än
   * assertade FreeRTOS och kortet bootloopade (hittat vid första flashen
   * 2026-08-06). Ordningen är en del av kontraktet, inte en detalj. */
  s_net_events = xEventGroupCreate();

  /* Kandidatlistans lås FÖRE wifi_start: event-handlern kan ta emot sitt
   * första DISCONNECTED innan app_main hunnit längre. */
  s_cand_lock = xSemaphoreCreateMutex();
  ESP_ERROR_CHECK(s_cand_lock ? ESP_OK : ESP_ERR_NO_MEM);

  /* Panelen först, nätet sedan: initsekvensen tar ~1,2 s och skärmen ska
   * visa sina streck medan WiFi:t kopplar upp, inte stå svart i tio
   * sekunder. Egen start med små flushbitar — se display_start ovan. */
  display_start();
  /* Panelen initierad utan fel = displaybeviset. Att den dessutom LYSER
   * verifieras fysiskt i uppgift 8 — grinden mäter det som går att mäta. */
  torget_boot_health_mark(TG_HEALTH_DISPLAY);
  /* Börja släckt: tick_cb:s ramp lyfter till dagsläge på ~1,3 s. Det är
   * bootens fade-in — samma ramp som nattväckningen använder. */
  bsp_display_brightness_set(0);
  /* s_touch sattes i display_start — BSP:ns accessor vet inget om vår start. */
  sg_rotation_start(s_touch); /* P24: bilden följer med när enheten vrids */

  /* KEY3 (GPIO18, aktiv låg enligt spec/hardware.md): intern pullup,
   * pollas av tick_cb som appväxlare. */
  gpio_config_t key3 = {
    .pin_bit_mask = 1ULL << GPIO_NUM_18,
    .mode = GPIO_MODE_INPUT,
    .pull_up_en = GPIO_PULLUP_ENABLE,
  };
  ESP_ERROR_CHECK(gpio_config(&key3));

  /* Boot räknas som aktivitet: skärmen får sina 15 min att visa upp sig
   * innan första nattdimningen, även om ingen app hunnit rapportera liv. */
  s_last_activity_us = esp_timer_get_time();

  torget_ui_lock();
  /* Bootskärmen FÖRE apparna och FÖRE OTA-overlayn: apparnas halvbyggda
   * NO DATA-vyer göms bakom den, och READY-ringen vinner alltid över den
   * i lagerordningen. */
  torget_boot_screen_create();
  torget_ui_create(); /* bygger apparna via registret + launchern */
  /* UI-beviset: registret, apparnas create() och launchern överlevde. */
  torget_boot_health_mark(TG_HEALTH_UI);
  /* Nätlagret EFTER apparna men FÖRE OTA-overlayn: båda bor på topplagret
   * och hämtar sig längst fram i sin egen set(), så skapelseordningen
   * avgör vem som vinner när båda vill synas. READY-ringen ska alltid
   * vinna — den skapas därför sist. */
  torget_wifi_ui_create();
  /* OTA-overlayn EFTER det delade UI:t, på topplagret, dold tills KEY3-
   * hållet öppnar underhållsfönstret — appträdet rörs aldrig. */
  torget_ota_ui_create();
  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);
  torget_ui_unlock();

  /* Fysisk sanning i loggen: KEY3:s råa nivå vid boot. Låg utan finger =
   * pinnen är inte att lita på förrän knappolicyns väpning släppt igenom
   * den (så hände 2026-08-14, då ett fönster öppnade sig självt). */
  ESP_LOGI(TAG, "KEY3 rå nivå vid boot: %d (1 = släppt)",
           gpio_get_level(GPIO_NUM_18));

  wifi_start();
  /* Nättasken FÖRE OTA-vakten: apparnas dataväg är plattformens kritiska
   * bana och får aldrig stå bakom en valfri funktion i minneskön. */
  if (xTaskCreate(net_task, "torget-net", 4096, NULL, 5, NULL) != pdPASS)
    ESP_LOGE(TAG, "torget-net kunde inte skapas — apparna får aldrig data");
  if (xTaskCreate(wifi_signal_task, "wifi-signal", 2048, NULL, 2, NULL) != pdPASS)
    ESP_LOGW(TAG, "wifi-signal kunde inte skapas — ikonen visar frånkopplad");
  /* OTA-ytan är LAT: vid boot startar bara den lilla fönstervakten.
   * Http-servern och dess minneskostnad existerar först när ett KEY3-håll
   * öppnat underhållsfönstret — en boot utan uppdatering ska ha samma
   * minnesprofil som en build helt utan OTA (frysläxan 2026-08-14). */
  torget_ota_service_start();
  /* Nätvakten sist och lika lat: accesspunkten, http-servern och
   * DNS-tasken existerar först när setupfönstret öppnats. En panel som
   * hittar sitt nät betalar ingenting för att funktionen finns. */
  torget_wifi_setup_start(&s_setup_hooks);
}
