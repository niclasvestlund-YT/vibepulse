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
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
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

#include "rotation.h"
#include "secrets.h"
#include "torget.h"

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

int64_t torget_now_us(void) { return esp_timer_get_time(); }

void torget_net_wait(void) {
  xEventGroupWaitBits(s_net_events, NET_READY, pdFALSE, pdTRUE, portMAX_DELAY);
}

void torget_keep_awake(void) { s_last_activity_us = esp_timer_get_time(); }

/* ------------------------------------------------------------------- wifi */

/* Bootdiagnostik, medvetet permanent: skanna EN gång från nättasken (inte
 * event-loopen — dess stack sväljer varken blockering eller AP-listan)
 * innan första connect. S3:an är 2,4 GHz-only, så ett nät som saknas i
 * listan finns inte i dess värld oavsett vad telefonen ser. En hyllpryl
 * utan skärmtangentbord kan inte felsöka WiFi på annat sätt än att berätta
 * vad den ser; 2,5 s extra vid boot är priset. */
static void scan_debug(void) {
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
    ESP_LOGI(TAG, "  (%d nät totalt; vårt mål: \"%s\")", n, TG_WIFI_SSID);
  }
}

static bool s_first_start = true;

/* Reservnätet (telefonens internetdelning — beslut 2026-08-07) är valfritt:
 * gamla secrets.h utan TG_WIFI2_* bygger oförändrat via tomma defaultar. */
#ifndef TG_WIFI2_SSID
#define TG_WIFI2_SSID ""
#define TG_WIFI2_PASS ""
#endif

static const char *const s_ssid[2] = { TG_WIFI_SSID, TG_WIFI2_SSID };
static const char *const s_pass[2] = { TG_WIFI_PASS, TG_WIFI2_PASS };
static int s_nat;         /* vilket av näten vi jagar just nu             */
static int s_nat_missar;  /* missar i rad på DET nätet                    */

/* 4 missar ≈ 10-15 s per nät innan vi provar det andra — snabbt nog för
 * bilen, trögt nog att inte fladdra när hemmanätet har en dålig stund. */
#define NAT_MISSAR_INNAN_BYTE 4

static void wifi_apply(int idx) {
  wifi_config_t cfg = { 0 };
  strlcpy((char *)cfg.sta.ssid, s_ssid[idx], sizeof cfg.sta.ssid);
  strlcpy((char *)cfg.sta.password, s_pass[idx], sizeof cfg.sta.password);
  cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
  (void)arg;
  if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
    if (s_first_start) { s_first_start = false; return; } /* nättasken sköter första */
    esp_wifi_connect();
  } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
    xEventGroupClearBits(s_net_events, WIFI_GOT_IP);
    /* Orsakskoden är diagnosen: 201 = nätet syns inte alls (fel namn, eller
     * bara 5 GHz — S3:an hör enbart 2,4 GHz), 15/204 = fel lösenord. */
    ESP_LOGW(TAG, "WiFi tappat (\"%s\", orsak %d), återansluter",
             s_ssid[s_nat], ((wifi_event_sta_disconnected_t *)data)->reason);
    /* Växelbruk hem/hotspot: efter NAT_MISSAR_INNAN_BYTE missar i rad provas
     * det andra nätet (om ifyllt). Ingen prioritet — det som svarar vinner,
     * och tappas det börjar jakten om. */
    if (s_ssid[1][0] != '\0' && ++s_nat_missar >= NAT_MISSAR_INNAN_BYTE) {
      s_nat = !s_nat;
      s_nat_missar = 0;
      ESP_LOGI(TAG, "provar \"%s\" i stället", s_ssid[s_nat]);
      wifi_apply(s_nat);
    }
    vTaskDelay(pdMS_TO_TICKS(2000));
    esp_wifi_connect();
  } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
    ESP_LOGI(TAG, "WiFi uppe (\"%s\")", s_ssid[s_nat]);
    s_nat_missar = 0;
    xEventGroupSetBits(s_net_events, WIFI_GOT_IP);
  }
}

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
  wifi_apply(0);                  /* hemmanätet först; växelbruket tar över */
  ESP_ERROR_CHECK(esp_wifi_start());
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
  scan_debug();
  esp_wifi_connect();
  xEventGroupWaitBits(s_net_events, WIFI_GOT_IP, pdFALSE, pdTRUE, portMAX_DELAY);
  time_sync();
  xEventGroupSetBits(s_net_events, NET_READY);
  vTaskDelete(NULL);
}

/* ------------------------------------------------------- LVGL-tasken, 10 Hz */

static void tick_cb(lv_timer_t *t) {
  (void)t;
  int64_t now = esp_timer_get_time();

  /* Minnestelemetri var 10:e sekund: SPI-flushen till panelen behöver
   * DMA-dugligt internminne, och tar det slut fastnar hela ritpipen i
   * NO_MEM (sett vid första flashen 2026-08-06: TLS-hämtning + omritning
   * sammanföll och panelen tystnade permanent). Largest block är siffran
   * som avgör — fragmentering syns inte i totalsumman. */
  static int heap_probe;
  if (++heap_probe >= 100) {
    heap_probe = 0;
    ESP_LOGI(TAG, "heap: internt %u fritt (största block %u, lägsta någonsin %u), DMA största %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA));
  }

  /* Väckning: ett finger på glaset räknas som aktivitet. Pollat, inte
   * event-kopplat — apparnas UI:n får sluka gesterna bäst de vill,
   * indev-tillståndet ser trycket ändå. */
  if (s_touch && lv_indev_get_state(s_touch) == LV_INDEV_STATE_PRESSED)
    s_last_touch_us = now;

  /* KEY3 (GPIO18, aktiv låg): fysisk appväxlare — ett tryck, nästa app.
   * Pollad i 10 Hz-ticken (naturlig avstudsning: en flank per nedtryck,
   * släpp krävs mellan). Körs i LVGL-tasken, så inga lås behövs. */
  static bool key3_was_down;
  bool key3_down = gpio_get_level(GPIO_NUM_18) == 0;
  if (key3_down && !key3_was_down) {
    torget_app_next();
    s_last_touch_us = now; /* ett knapptryck är aktivitet, precis som touch */
  }
  key3_was_down = key3_down;

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

void app_main(void) {
  esp_err_t nvs = nvs_flash_init();
  if (nvs == ESP_ERR_NVS_NO_FREE_PAGES || nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    nvs = nvs_flash_init();
  }
  ESP_ERROR_CHECK(nvs);

  /* Eventgruppen FÖRE UI-bygget: apparnas hämttasker startar i create()
   * och blockerar direkt i torget_net_wait() — fanns gruppen inte än
   * assertade FreeRTOS och kortet bootloopade (hittat vid första flashen
   * 2026-08-06). Ordningen är en del av kontraktet, inte en detalj. */
  s_net_events = xEventGroupCreate();

  /* Panelen först, nätet sedan: initsekvensen tar ~1,2 s och skärmen ska
   * visa sina streck medan WiFi:t kopplar upp, inte stå svart i tio
   * sekunder. Egen start med små flushbitar — se display_start ovan. */
  display_start();
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
  torget_ui_create(); /* bygger apparna via registret + launchern */
  lv_timer_create(tick_cb, TICK_EVERY_MS, NULL);
  torget_ui_unlock();

  wifi_start();
  xTaskCreate(net_task, "torget-net", 4096, NULL, 5, NULL);
}
