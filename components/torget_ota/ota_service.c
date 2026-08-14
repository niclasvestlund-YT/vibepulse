#include "ota_service.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_app_desc.h"
#include "esp_app_format.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"

#include "notice_policy.h"
#include "ota_policy.h"
#include "ota_ui.h"
#include "secrets.h"

static const char *TAG = "ota-service";

/* Tokenkontraktet: exakt 64 små hextecken, definierat i secrets.h (utanför
 * git). Utan makrot förblir uppladdningen avstängd — statusendpointen
 * lever ändå, så en skärm utan token går fortfarande att inspektera. */
#ifdef TG_OTA_TOKEN
_Static_assert(sizeof(TG_OTA_TOKEN) == 65,
               "TG_OTA_TOKEN must be 64 hex characters");
#endif

/* Tio minuter per KEY3-håll: långt nog för bygge + uppladdning, kort nog
 * att en bortglömd öppning stänger sig själv innan kvällen är slut. */
#define MAINTENANCE_WINDOW_US (10LL * 60LL * 1000000LL)

/* Så mycket av bildens början som metadatan kräver: imageheader (24 B),
 * första segmentheadern (8 B) och appbeskrivningen (256 B). Kortare kropp
 * än så kan aldrig vara en Torget-avbild. */
#define IMAGE_PREFIX_BYTES (sizeof(esp_image_header_t) + \
                            sizeof(esp_image_segment_header_t) + \
                            sizeof(esp_app_desc_t))

/* EN fast intern buffert för hela strömmen (4096 B per httpd_req_recv).
 * Statisk .bss = internminne; uppladdningsvakten nedan gör den enkel-
 * användar-säker utan lås. */
#define CHUNK_BYTES 4096
static uint8_t s_chunk[CHUNK_BYTES];

static httpd_handle_t s_server;
static bool s_token_usable;
static esp_timer_handle_t s_reboot_timer;

/* Fönstret och uppladdningsvakten är atomära: tick_cb (LVGL-tasken)
 * öppnar, httpd-tasken laddar, underhållstasken ritar — ingen av dem får
 * blockera någon annan för att läsa ett tillstånd. */
static _Atomic int64_t s_maintenance_until_us;
static atomic_bool s_upload_active;

void torget_ota_service_open_maintenance(void) {
  atomic_store(&s_maintenance_until_us,
               esp_timer_get_time() + MAINTENANCE_WINDOW_US);
  ESP_LOGI(TAG, "underhållsfönstret öppet i tio minuter");
}

bool torget_ota_service_maintenance_open(void) {
  return esp_timer_get_time() < atomic_load(&s_maintenance_until_us);
}

void torget_ota_service_close_maintenance(void) {
  /* Sätt fönstret till "utgånget men inte noll": vakten ser skillnaden på
   * sin nästa halvsekundpoll, gömmer overlayn och stoppar servern i sin
   * egen task. Att skriva 0 direkt hade lämnat overlayn synlig för evigt,
   * och att röra LVGL härifrån (LVGL-tasken) är förbjudet. */
  int64_t open_until = atomic_load(&s_maintenance_until_us);
  if (open_until > 1) atomic_store(&s_maintenance_until_us, 1);
}

/* Konstanttidsjämförelse: varje byte XOR:as och OR:as ihop, så tiden är
 * densamma oavsett var första avvikelsen sitter — svarstiden får inte
 * läcka hur många inledande tecken av token som stämde. */
static bool constant_time_equal(const char *a, const char *b, size_t n) {
  unsigned char diff = 0;
  for (size_t i = 0; i < n; i++)
    diff |= (unsigned char)a[i] ^ (unsigned char)b[i];
  return diff == 0;
}

#ifdef TG_OTA_TOKEN
/* Teckenuppsättningen valideras EN gång vid start; ett trasigt token ska
 * synas i loggen vid boot, inte som gåtfulla 401 vid midnatt. Själva
 * värdet skrivs aldrig ut och returneras aldrig. */
static bool token_charset_valid(void) {
  for (const char *p = TG_OTA_TOKEN; *p; p++) {
    bool hex = (*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f');
    if (!hex) return false;
  }
  return true;
}
#endif

/* 64 små hextecken → 32 byte. Ordningen stor bokstav = fel är avsiktlig:
 * kontraktet säger gemener, och ett kontrakt man tänjer på slutar gälla. */
static bool parse_sha256_hex(const char *hex, uint8_t out[32]) {
  for (int i = 0; i < 64; i++) {
    char c = hex[i];
    int nibble;
    if (c >= '0' && c <= '9') nibble = c - '0';
    else if (c >= 'a' && c <= 'f') nibble = c - 'a' + 10;
    else return false;
    if (i % 2 == 0) out[i / 2] = (uint8_t)(nibble << 4);
    else out[i / 2] |= (uint8_t)nibble;
  }
  return hex[64] == '\0';
}

/* Avslag stänger anslutningen (ESP_FAIL): en okonsumerad kropp får aldrig
 * ligga kvar och förgiftas in i nästa requesttolkning. */
static esp_err_t reject(httpd_req_t *req, const char *status,
                        const char *reason) {
  char body[96];
  snprintf(body, sizeof body, "{\"error\":\"%s\"}", reason);
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_sendstr(req, body);
  return ESP_FAIL;
}

/* ------------------------------------------------------- GET /api/ota/status */

static esp_err_t status_get_handler(httpd_req_t *req) {
  const esp_app_desc_t *desc = esp_app_get_description();
  const esp_partition_t *running = esp_ota_get_running_partition();
  char body[224];
  /* Bara fakta enheten själv äger — aldrig token. Saknad partitionsetikett
   * blir ett streck, aldrig ett påhitt (ärlighetsinvarianten). */
  snprintf(body, sizeof body,
           "{\"project\":\"torget\",\"chip\":\"esp32s3\","
           "\"maintenance_open\":%s,\"max_image_bytes\":%u,"
           "\"running_version\":\"%s\",\"running_partition\":\"%s\"}",
           torget_ota_service_maintenance_open() ? "true" : "false",
           (unsigned)TG_OTA_MAX_IMAGE_BYTES, desc->version,
           running ? running->label : "–");
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_sendstr(req, body);
}

/* ---------------------------------------------------- POST /api/ota/firmware */

static void reboot_timer_cb(void *arg) {
  (void)arg;
  esp_restart();
}

/* Metadatagrinden på första chunken: rätt ESP-imagemagi, rimligt antal
 * segment, rätt chip, äkta appbeskrivning och rätt projekt. Fel avbild
 * (Vibbe? bootloadern? en godtycklig fil?) stoppas innan mer än en
 * buffert nått flashen — och den luckan var ändå inaktiv. */
static bool image_prefix_valid(const uint8_t *prefix, char *version_out,
                               size_t version_cap) {
  esp_image_header_t image;
  memcpy(&image, prefix, sizeof image);
  if (image.magic != ESP_IMAGE_HEADER_MAGIC) return false;
  if (image.segment_count == 0 || image.segment_count > 16) return false;
  if (image.chip_id != ESP_CHIP_ID_ESP32S3) return false;

  esp_app_desc_t app;
  memcpy(&app, prefix + sizeof(esp_image_header_t) +
                   sizeof(esp_image_segment_header_t), sizeof app);
  if (app.magic_word != ESP_APP_DESC_MAGIC_WORD) return false;
  if (strncmp(app.project_name, "torget", sizeof app.project_name) != 0)
    return false;
  snprintf(version_out, version_cap, "%.*s",
           (int)sizeof app.version, app.version);
  return true;
}

static esp_err_t firmware_post_handler(httpd_req_t *req) {
  /* En uppladdning i taget: bufferten och den inaktiva luckan är ensamma
   * resurser. Vakten släpps på varje felväg; på framgång behålls den tills
   * omstartstimern tar enheten. */
  bool idle = false;
  if (!atomic_compare_exchange_strong(&s_upload_active, &idle, true))
    return reject(req, "503 Service Unavailable", "upload already running");

  /* Auktorisering FÖRE policyfrågan: saknat, trasigt eller fel token blir
   * authorized=false, och policyns ordning (CLOSED > AUTH > ...) avgör
   * vilket avslag som syns utåt. Headern loggas aldrig. */
  bool authorized = false;
#ifdef TG_OTA_TOKEN
  char authorization[7 + 64 + 1];
  if (s_token_usable &&
      httpd_req_get_hdr_value_str(req, "Authorization", authorization,
                                  sizeof authorization) == ESP_OK &&
      strncmp(authorization, "Bearer ", 7) == 0 &&
      strlen(authorization + 7) == 64) {
    authorized = constant_time_equal(authorization + 7, TG_OTA_TOKEN, 64);
  }
#endif

  char project[24] = {0};
  char chip[24] = {0};
  bool has_project = httpd_req_get_hdr_value_str(
                         req, "X-VibePulse-Project", project,
                         sizeof project) == ESP_OK;
  bool has_chip = httpd_req_get_hdr_value_str(req, "X-VibePulse-Chip", chip,
                                              sizeof chip) == ESP_OK;

  const esp_partition_t *update = esp_ota_get_next_update_partition(NULL);
  if (!update) {
    atomic_store(&s_upload_active, false);
    return reject(req, "500 Internal Server Error", "no inactive slot");
  }

  tg_ota_request request = {
      .maintenance_open = torget_ota_service_maintenance_open(),
      .authorized = authorized,
      .project = has_project ? project : NULL,
      .chip = has_chip ? chip : NULL,
      .content_length = req->content_len,
      .slot_size = update->size,
  };
  switch (tg_ota_request_check(&request)) {
    case TG_OTA_ACCEPT:
      break;
    case TG_OTA_REJECT_CLOSED:
      atomic_store(&s_upload_active, false);
      return reject(req, "403 Forbidden", "maintenance window closed");
    case TG_OTA_REJECT_AUTH:
      atomic_store(&s_upload_active, false);
      return reject(req, "401 Unauthorized", "bad or missing token");
    case TG_OTA_REJECT_PROJECT:
      atomic_store(&s_upload_active, false);
      return reject(req, "400 Bad Request", "wrong project");
    case TG_OTA_REJECT_CHIP:
      atomic_store(&s_upload_active, false);
      return reject(req, "400 Bad Request", "wrong chip");
    case TG_OTA_REJECT_SIZE:
      atomic_store(&s_upload_active, false);
      return reject(req, "413 Payload Too Large", "image size out of bounds");
  }

  /* SHA-headern efter policyn: avslagsordningen ovan är kontrakt, och en
   * stängd lucka ska svara 403 även på en trasig digest. */
  char sha_hex[65];
  uint8_t expected_sha[32];
  if (httpd_req_get_hdr_value_str(req, "X-VibePulse-SHA256", sha_hex,
                                  sizeof sha_hex) != ESP_OK ||
      !parse_sha256_hex(sha_hex, expected_sha)) {
    atomic_store(&s_upload_active, false);
    return reject(req, "400 Bad Request", "sha256 header must be 64 hex");
  }

  if (req->content_len < IMAGE_PREFIX_BYTES) {
    atomic_store(&s_upload_active, false);
    return reject(req, "400 Bad Request", "body too small for an app image");
  }

  ESP_LOGI(TAG, "uppladdning: %u byte mot %s", (unsigned)req->content_len,
           update->label);

  esp_ota_handle_t ota = 0;
  esp_err_t err = esp_ota_begin(update, req->content_len, &ota);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_ota_begin: %s", esp_err_to_name(err));
    atomic_store(&s_upload_active, false);
    return reject(req, "500 Internal Server Error", "ota begin failed");
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);

  char new_version[32] = {0};
  size_t received = 0;
  size_t fill = 0;          /* prefixinsamling tills metadatan kan avgöras */
  bool prefix_checked = false;
  torget_ota_ui_set(TG_OTA_UI_RECEIVING, 0, 0);

  while (received < req->content_len) {
    /* fill räknar prefixbyte som redan lästs ur sockeln men ännu inte
     * skrivits till flash — utan den termen skulle vi be om för mycket. */
    size_t want = req->content_len - received - fill;
    if (want > CHUNK_BYTES - fill) want = CHUNK_BYTES - fill;
    int got = httpd_req_recv(req, (char *)s_chunk + fill, want);
    if (got <= 0) {
      /* Timeout, frånkoppling eller kort kropp: avbryt och behåll nuvarande
       * bootlucka. esp_ota_abort lämnar otadata orört. */
      mbedtls_sha256_free(&sha);
      esp_ota_abort(ota);
      atomic_store(&s_upload_active, false);
      ESP_LOGW(TAG, "strömmen bröts efter %u byte", (unsigned)received);
      return reject(req,
                    got == HTTPD_SOCK_ERR_TIMEOUT ? "408 Request Timeout"
                                                  : "400 Bad Request",
                    "upload interrupted");
    }

    if (!prefix_checked) {
      fill += (size_t)got;
      if (fill < IMAGE_PREFIX_BYTES) continue; /* samla tills grinden kan dömas */
      if (!image_prefix_valid(s_chunk, new_version, sizeof new_version)) {
        mbedtls_sha256_free(&sha);
        esp_ota_abort(ota);
        atomic_store(&s_upload_active, false);
        return reject(req, "400 Bad Request", "not a torget esp32s3 image");
      }
      prefix_checked = true;
      /* Metadatan är läst: berätta på glaset VAD som är på väg in — den
       * inkommande avbildens egen versionssträng, aldrig en gissning. */
      torget_ota_ui_set_version(new_version);
      got = (int)fill; /* skriv hela det insamlade prefixet i ett svep */
      fill = 0;
    }

    mbedtls_sha256_update(&sha, s_chunk, (size_t)got);
    err = esp_ota_write(ota, s_chunk, (size_t)got);
    if (err != ESP_OK) {
      mbedtls_sha256_free(&sha);
      esp_ota_abort(ota);
      atomic_store(&s_upload_active, false);
      ESP_LOGE(TAG, "esp_ota_write: %s", esp_err_to_name(err));
      return reject(req, "500 Internal Server Error", "flash write failed");
    }
    received += (size_t)got;
    torget_ota_ui_set(TG_OTA_UI_RECEIVING,
                      tg_ota_progress_percent(received, req->content_len), 0);
  }

  torget_ota_ui_set(TG_OTA_UI_VERIFYING, 100, 0);

  uint8_t actual_sha[32];
  mbedtls_sha256_finish(&sha, actual_sha);
  mbedtls_sha256_free(&sha);
  if (memcmp(actual_sha, expected_sha, sizeof actual_sha) != 0) {
    esp_ota_abort(ota);
    atomic_store(&s_upload_active, false);
    ESP_LOGE(TAG, "SHA-256 stämmer inte: avbilden förkastad");
    return reject(req, "400 Bad Request", "sha256 mismatch");
  }

  /* esp_ota_end validerar avbildens struktur; först därefter — och aldrig
   * före — får den inaktiva luckan pekas ut som nästa boot. Bootloader,
   * partitionstabell, NVS, PHY och den aktiva luckan skrivs aldrig. */
  err = esp_ota_end(ota);
  if (err != ESP_OK) {
    atomic_store(&s_upload_active, false);
    ESP_LOGE(TAG, "esp_ota_end: %s", esp_err_to_name(err));
    return reject(req, "500 Internal Server Error", "image validation failed");
  }
  err = esp_ota_set_boot_partition(update);
  if (err != ESP_OK) {
    atomic_store(&s_upload_active, false);
    ESP_LOGE(TAG, "esp_ota_set_boot_partition: %s", esp_err_to_name(err));
    return reject(req, "500 Internal Server Error", "slot select failed");
  }

  ESP_LOGI(TAG, "avbild %s vald för nästa boot (version %s)", update->label,
           new_version);
  torget_ota_ui_set(TG_OTA_UI_RESTARTING, 100, 0);

  char body[144];
  snprintf(body, sizeof body, "{\"sha256\":\"%s\",\"version\":\"%s\"}",
           sha_hex, new_version);
  httpd_resp_set_status(req, "202 Accepted");
  httpd_resp_set_type(req, "application/json");
  httpd_resp_sendstr(req, body);

  /* Svaret först, omstarten strax efter: klienten ska hinna läsa sitt 202
   * innan länken dör. Uppladdningsvakten släpps ALDRIG här — hälsogrinden
   * på andra sidan omstarten är nästa instans. */
  const esp_timer_create_args_t args = {
      .callback = reboot_timer_cb,
      .name = "ota-reboot",
  };
  if (esp_timer_create(&args, &s_reboot_timer) == ESP_OK)
    esp_timer_start_once(s_reboot_timer, 1500 * 1000);
  else
    esp_restart(); /* hellre en abrupt omstart än en vald lucka som aldrig bootas */
  return ESP_OK;
}

/* -------------------------------------------------------- OTA-annonsen */

/* Annonsen skrivs av appens hämttask (via torget_update_available i main)
 * och läses av vakten — en kort kritisk sektion räcker för en 32-bytes
 * sträng som byts på sin höjd en gång per bygge. */
static portMUX_TYPE s_notice_mux = portMUX_INITIALIZER_UNLOCKED;
static char s_announced_version[32];
static bool s_has_announcement;

void torget_ota_service_update_available(const char *version) {
  taskENTER_CRITICAL(&s_notice_mux);
  if (version && version[0]) {
    strlcpy(s_announced_version, version, sizeof s_announced_version);
    s_has_announcement = true;
  } else {
    s_has_announcement = false;
  }
  taskEXIT_CRITICAL(&s_notice_mux);
}

static bool notice_announced_copy(char *out, size_t capacity) {
  taskENTER_CRITICAL(&s_notice_mux);
  bool has = s_has_announcement;
  if (has) strlcpy(out, s_announced_version, capacity);
  taskEXIT_CRITICAL(&s_notice_mux);
  return has;
}

/* ------------------------------------------------- fönstrets UI-pollning */

/* Overlayn drivs härifrån och från httpd-handlern — ALDRIG från LVGL-
 * tasken, eftersom torget_ota_ui_set tar UI-låset självt. En halvsekunds
 * poll räcker: nedräkningen är i hela sekunder och dedupe:n i ota_ui gör
 * oförändrade poll gratis. */
static void httpd_surface_start(void);
static void httpd_surface_stop(void);

static void maintenance_ui_task(void *arg) {
  (void)arg;
  static tg_notice_policy notice;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(500));
    if (atomic_load(&s_upload_active)) continue; /* handlern äger overlayn */
    int64_t until = atomic_load(&s_maintenance_until_us);

    /* UPDATE READY-notisen körs FÖRE fönsterlogiken: upptaget läge
     * (öppet fönster/överföring) trycker undan takeovern via policyn,
     * och fönsterbranchen nedan skriver ändå sitt eget läge efteråt. */
    int64_t now_us = esp_timer_get_time();
    char announced[32];
    bool has_announced = notice_announced_copy(announced, sizeof announced);
    bool differs = has_announced &&
        strncmp(announced, esp_app_get_description()->version,
                sizeof announced) != 0;
    /* Trycken draneras VARJE varv (overlayn ar klickbar i alla lagen och
     * en kvarliggande flagga fran t.ex. OPEN skulle sjalvsnooza nasta
     * notis), men de betyder nagot bara nar notisen visas. */
    bool tap_yes = torget_ota_ui_take_update_tap();
    bool tap_snooze = torget_ota_ui_take_tap();
    if (notice.showing && tap_yes) {
      /* JA pa glaset = samma fysiska samtycke som KEY3-hallet: oppna
       * fonstret sa pusharen pa Macen far leverera. Tokenet vaktar
       * fortfarande sjalva uppladdningen. */
      ESP_LOGI(TAG, "notisen besvarad med JA — fönstret öppnas");
      tg_notice_dismiss(&notice, now_us);
      torget_ota_service_open_maintenance();
      until = atomic_load(&s_maintenance_until_us);
    } else if (notice.showing && tap_snooze) {
      tg_notice_dismiss(&notice, now_us);
    }
    bool busy = until != 0 && (until - now_us) > 0;
    switch (tg_notice_update(&notice, differs, busy, now_us)) {
      case TG_NOTICE_SHOW:
        ESP_LOGI(TAG, "uppdatering annonserad (%s) — notisen tar glaset",
                 announced);
        torget_ota_ui_set_version(announced);
        torget_ota_ui_set(TG_OTA_UI_NOTICE, 0, 0);
        break;
      case TG_NOTICE_HIDE:
        torget_ota_ui_set(TG_OTA_UI_HIDDEN, 0, 0);
        break;
      case TG_NOTICE_NONE:
        break;
    }

    if (until == 0) continue;
    int64_t left_us = until - esp_timer_get_time();
    if (left_us > 0) {
      /* Lat yta: servern och dess minneskostnad föds först nu, när ett
       * håll bevisligen öppnat fönstret. En boot utan uppdatering bär
       * därmed ingen httpd alls (frysläxan 2026-08-14). */
      httpd_surface_start();
      /* Versionsraden svarar "vad kör enheten NU?" så länge fönstret
       * väntar — och återpushen varje poll självläker raden efter en
       * avbruten uppladdning som hann visa sin inkommande version. */
      torget_ota_ui_set_version(esp_app_get_description()->version);
      torget_ota_ui_set(TG_OTA_UI_OPEN, 0,
                        (int)((left_us + 999999) / 1000000));
    } else if (atomic_compare_exchange_strong(&s_maintenance_until_us,
                                              &until, 0)) {
      /* Fönstret gick ut eller stängdes i förtid: göm overlayn och lämna
       * tillbaka serverns minne. Bytet sker atomärt så ett nytt KEY3-håll
       * som hann emellan aldrig nollas bort. */
      torget_ota_ui_set(TG_OTA_UI_HIDDEN, 0, 0);
      httpd_surface_stop();
      ESP_LOGI(TAG, "underhållsfönstret stängt");
    }
  }
}

/* ------------------------------------------------------------------ start */

static void httpd_surface_start(void) {
  if (s_server) return; /* idempotent: vakten pollar varje halvsekund */

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = TG_OTA_HTTP_PORT;
  /* SHA-kontext, esp_ota-anrop och svarsbyggen bor på handlerstacken. */
  config.stack_size = 8192;
  config.lru_purge_enable = true;
  /* En uppladdare + en statuskoll. Lwip-budgeten är 10 sockar totalt för
   * hela enheten — apparnas hämtningar får aldrig stå utan för att en
   * LAN-scanner höll OTA-porten sysselsatt. */
  config.max_open_sockets = 2;
  esp_err_t err = httpd_start(&s_server, &config);
  if (err != ESP_OK) {
    s_server = NULL;
    ESP_LOGE(TAG, "httpd_start: %s", esp_err_to_name(err));
    return;
  }

  static const httpd_uri_t status_uri = {
      .uri = "/api/ota/status",
      .method = HTTP_GET,
      .handler = status_get_handler,
  };
  static const httpd_uri_t firmware_uri = {
      .uri = "/api/ota/firmware",
      .method = HTTP_POST,
      .handler = firmware_post_handler,
  };
  httpd_register_uri_handler(s_server, &status_uri);
  httpd_register_uri_handler(s_server, &firmware_uri);
  ESP_LOGI(TAG, "OTA-lyssnaren uppe på port %d", TG_OTA_HTTP_PORT);
}

static void httpd_surface_stop(void) {
  if (!s_server) return;
  httpd_stop(s_server);
  s_server = NULL;
  ESP_LOGI(TAG, "OTA-lyssnaren stoppad — minnet åter till apparna");
}

void torget_ota_service_start(void) {
  static bool s_started;
  if (s_started) return; /* idempotent: wifi-omstarter får inte dubblera oss */
  s_started = true;

#ifdef TG_OTA_TOKEN
  s_token_usable = token_charset_valid();
  if (!s_token_usable)
    ESP_LOGE(TAG, "OTA-tokenet i secrets.h är inte 64 små hextecken — "
                  "uppladdning avstängd");
#else
  s_token_usable = false;
  ESP_LOGI(TAG, "inget OTA-token i secrets.h — uppladdning avstängd");
#endif

  /* Vid boot startar ENDAST vakten. Http-servern föds i vaktens task när
   * fönstret öppnas och dör när det stängs — en boot utan uppdatering ska
   * ha samma minnesprofil som en build helt utan OTA.
   *
   * 8192, inte 3072: tasken kör lv_label_set_text/layout under UI-låset,
   * och LVGL:s textmotor åt upp 3 KB på riktig panel — tasken dog med
   * låset i handen och frös hela glaset (2026-08-14, första fönstret). */
  if (xTaskCreate(maintenance_ui_task, "ota-ui", 8192, NULL, 3, NULL) !=
      pdPASS) {
    ESP_LOGE(TAG, "ota-vakten kunde inte skapas — OTA avstängd denna boot");
  }

  /* Första boot på en nyss OTA:ad avbild (PENDING_VERIFY) återöppnar
   * fönstret av sig självt: den som itererar (flasha, prova, flasha igen)
   * ska hålla KEY3 EN gång per arbetspass, inte en gång per bygge
   * (beslut 2026-08-14). Samtycket är inte urholkat — kedjan startade i
   * ett fysiskt håll, fönstret är lika tidsbegränsat som annars och ett
   * kort tryck stänger det. En esptool-flashad boot (UNDEFINED/VALID) har
   * inget föregående håll och lämnas stängd. */
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  if (esp_ota_get_state_partition(running, &state) == ESP_OK &&
      state == ESP_OTA_IMG_PENDING_VERIFY) {
    ESP_LOGI(TAG, "nyss uppdaterad avbild: underhållsfönstret återöppnas");
    torget_ota_service_open_maintenance();
  }
}
