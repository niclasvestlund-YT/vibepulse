#include "wifi_setup.h"

#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "lwip/sockets.h"

#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "mbedtls/sha256.h"

#include "ota_service.h"
#include "secrets.h"
#include "wifi_creds.h"
#include "wifi_form.h"
#include "wifi_setup_ui.h"
#include "wifi_slots.h"

static const char *TAG = "wifi-setup";

#define AP_SSID     "VibePulse-setup"
#define AP_ADDRESS  "192.168.4.1" /* esp_netif:s default för AP-gränssnittet */
#define AP_CHANNEL  1
#define AP_MAX_CONN 2

/* Så många nät setupsidan listar. Fler än så är en rullningslista ingen
 * orkar läsa, och listan bor i .bss — inte på en tasks stack. */
#define SCAN_MAX 16

static struct {
  char ssid[SCAN_MAX][TG_WIFI_SSID_CAP];
  int8_t rssi[SCAN_MAX];
  wifi_auth_mode_t authmode[SCAN_MAX];
  int n;
} s_scan;

static bool authmode_requires_password(wifi_auth_mode_t authmode) {
  return authmode != WIFI_AUTH_OPEN && authmode != WIFI_AUTH_OWE;
}

static const tg_wifi_setup_hooks *s_hooks;
static httpd_handle_t s_server;
static esp_netif_t *s_ap_netif;
static char s_ap_pass[13]; /* 12 hex + NUL */

/* Fönstrets tillstånd ägs av vakttasken; flaggorna korsar taskgränser och
 * är därför atomära (samma disciplin som ota_service). */
static atomic_bool s_open;
static atomic_bool s_want_open;
static atomic_bool s_want_close;
static atomic_bool s_dns_stop;
static _Atomic tg_wifi_setup_phase s_phase;
static TaskHandle_t s_guard_task;
static SemaphoreHandle_t s_join_lock;
static struct {
  char ssid[TG_WIFI_SSID_CAP];
  char pass[TG_WIFI_PASS_CAP];
  uint32_t seq;
} s_join_submission;
static _Atomic tg_wifi_join_status s_join_status;

static void join_submission_clear(void) {
  if (!s_join_lock) return;
  xSemaphoreTake(s_join_lock, portMAX_DELAY);
  memset(s_join_submission.ssid, 0, sizeof s_join_submission.ssid);
  memset(s_join_submission.pass, 0, sizeof s_join_submission.pass);
  s_join_submission.seq = 0;
  xSemaphoreGive(s_join_lock);
  atomic_store(&s_join_status, TG_WIFI_JOIN_IDLE);
}

/* ------------------------------------------------------ AP-lösenordet */

/*
 * Med TG_OTA_TOKEN i secrets.h HÄRLEDS lösenordet ur token, så
 * tools/wifi-here.sh kan räkna fram exakt samma sträng på Macen och
 * ansluta utan att någon läser av glaset. Det ger ingen ny behörighet:
 * den som redan har token kan skriva firmware till panelen.
 *
 * Utan token blir lösenordet slumpat per fönster och finns bara på glaset.
 * Domänsträngen gör härledningen skild från token självt — samma hemlighet,
 * två separata nycklar.
 */
static void derive_ap_password(void) {
#ifdef TG_OTA_TOKEN
  static const char domain[] = "vibepulse-softap-v1";
  unsigned char digest[32];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  mbedtls_sha256_update(&ctx, (const unsigned char *)domain, sizeof domain - 1);
  mbedtls_sha256_update(&ctx, (const unsigned char *)TG_OTA_TOKEN,
                        strlen(TG_OTA_TOKEN));
  mbedtls_sha256_finish(&ctx, digest);
  mbedtls_sha256_free(&ctx);
  for (int i = 0; i < 6; i++)
    snprintf(s_ap_pass + i * 2, 3, "%02x", digest[i]);
#else
  for (int i = 0; i < 6; i++)
    snprintf(s_ap_pass + i * 2, 3, "%02x", (unsigned)(esp_random() & 0xFF));
#endif
  s_ap_pass[12] = '\0';
}

/* ------------------------------------------------------------- skanning */

static void scan_networks(void) {
  s_scan.n = 0;
  if (esp_wifi_scan_start(NULL, true) != ESP_OK) {
    ESP_LOGW(TAG, "skanningen gick inte att starta");
    return;
  }
  static wifi_ap_record_t ap[SCAN_MAX]; /* .bss, inte stacken */
  uint16_t n = SCAN_MAX;
  if (esp_wifi_scan_get_ap_records(&n, ap) != ESP_OK) return;

  for (int i = 0; i < (int)n && s_scan.n < SCAN_MAX; i++) {
    const char *ssid = (const char *)ap[i].ssid;
    /* Dolda nät har tomt SSID och kan inte väljas ur en lista; ett SSID
     * med styrtecken hör inte hemma i HTML:en (upstream är fientlig). */
    if (!tg_wifi_ssid_valid(ssid)) continue;
    bool dupe = false;
    for (int j = 0; j < s_scan.n; j++)
      if (strcmp(s_scan.ssid[j], ssid) == 0) dupe = true;
    if (dupe) continue;
    snprintf(s_scan.ssid[s_scan.n], TG_WIFI_SSID_CAP, "%s", ssid);
    s_scan.rssi[s_scan.n] = ap[i].rssi;
    s_scan.authmode[s_scan.n] = ap[i].authmode;
    s_scan.n++;
  }

  /* Starkast först. Nätet man står bredvid ska ligga överst i listan, inte
   * på plats nio bland grannarnas — och det är panelens signalstyrka som
   * gäller, inte telefonens. Insättningssortering på högst 16 poster.
   *
   * memcpy, inte snprintf: raderna är fasta och åtskilda, men båda bor i
   * s_scan och GCC:s restrict-analys avvisar (med rätta) en strängkopiering
   * där käll- och målobjekt är samma. En radflytt är en blockkopia. */
  for (int i = 1; i < s_scan.n; i++) {
    char ssid[TG_WIFI_SSID_CAP];
    memcpy(ssid, s_scan.ssid[i], TG_WIFI_SSID_CAP);
    int8_t rssi = s_scan.rssi[i];
    wifi_auth_mode_t authmode = s_scan.authmode[i];
    int j = i - 1;
    while (j >= 0 && s_scan.rssi[j] < rssi) {
      memmove(s_scan.ssid[j + 1], s_scan.ssid[j], TG_WIFI_SSID_CAP);
      s_scan.rssi[j + 1] = s_scan.rssi[j];
      s_scan.authmode[j + 1] = s_scan.authmode[j];
      j--;
    }
    memcpy(s_scan.ssid[j + 1], ssid, TG_WIFI_SSID_CAP);
    s_scan.rssi[j + 1] = rssi;
    s_scan.authmode[j + 1] = authmode;
  }
  ESP_LOGI(TAG, "setupfönstret ser %d nät", s_scan.n);
}

/* ------------------------------------------------------------ http-sidan */

static const char PAGE_HEAD[] =
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>VibePulse</title><style>"
    "body{font-family:-apple-system,system-ui,sans-serif;background:#0b0b0c;"
    "color:#eaeaea;margin:0;padding:28px 20px;max-width:420px}"
    "h1{font-size:19px;letter-spacing:.08em;text-transform:uppercase}"
    "p{color:#9298a2;font-size:14px;line-height:1.5}"
    "label{display:block;color:#c8cbd1;font-size:14px;margin-top:12px}"
    "select,input,button{font-size:17px;padding:12px;width:100%;"
    "box-sizing:border-box;margin:6px 0;border-radius:10px;"
    "border:1px solid #303238;background:#151517;color:#eaeaea}"
    "button{background:#eaeaea;color:#0b0b0c;font-weight:600;border:0;"
    "margin-top:14px}"
    "</style></head><body><h1>VibePulse</h1>"
    "<p>Pick a 2.4 GHz network. It is saved only after the panel connects "
    "successfully. 2.4 GHz only&mdash;5 GHz networks are not visible.</p>"
    "<form method=\"POST\" action=\"/join\" "
    "onsubmit=\"this.querySelector('button').disabled=true\">"
    "<label for=\"ssid\">Wi-Fi network</label>"
    "<select id=\"ssid\">";

static const char PAGE_TAIL[] =
    "</select>"
    "<div id=\"manual-wrap\" hidden><label for=\"manual\">"
    "Network name</label><input id=\"manual\" type=\"text\" maxlength=\"32\" "
    "autocapitalize=\"off\" autocorrect=\"off\" autocomplete=\"off\" "
    "placeholder=\"Type the 2.4 GHz network name\"></div>"
    "<input id=\"ssid-value\" name=\"ssid\" type=\"hidden\">"
    "<div id=\"pass-wrap\"><label id=\"pass-label\" for=\"pass\">"
    "Wi-Fi password</label>"
    "<input id=\"pass\" name=\"pass\" type=\"password\" autocapitalize=\"off\" "
    "autocorrect=\"off\" minlength=\"8\" maxlength=\"63\" "
    "placeholder=\"Enter Wi-Fi password\"></div>"
    "<p id=\"open-note\" hidden>No password required</p>"
    "<button id=\"join\" type=\"submit\">Join</button></form><script>"
    "const ssid=document.getElementById('ssid'),pass=document.getElementById('pass'),"
    "manual=document.getElementById('manual'),"
    "manualWrap=document.getElementById('manual-wrap'),"
    "ssidValue=document.getElementById('ssid-value'),"
    "passWrap=document.getElementById('pass-wrap'),"
    "passLabel=document.getElementById('pass-label'),"
    "openNote=document.getElementById('open-note'),"
    "join=document.getElementById('join');"
    "function syncPassword(){const option=ssid.options[ssid.selectedIndex],"
    "typed=!!option&&option.value==='__manual__',"
    "hasNetwork=typed?manual.value.trim().length>0:!!option&&!option.disabled;"
    "manualWrap.hidden=!typed;manual.required=typed;"
    "ssidValue.value=typed?manual.value:(option&&!option.disabled?option.value:'');"
    "join.disabled=!hasNetwork;"
    "if(!hasNetwork){passWrap.hidden=true;openNote.hidden=true;"
    "pass.required=false;pass.disabled=true;pass.value='';return;}"
    "const secured=typed||option.dataset.secured==='1';"
    "passWrap.hidden = !secured;openNote.hidden=secured;"
    "pass.required = secured;pass.disabled=!secured;"
    "passLabel.textContent=secured?'Password for '+"
    "(typed?manual.value:option.text):'Wi-Fi password';"
    "if(!secured)pass.value='';}"
    "ssid.addEventListener('change',syncPassword);"
    "manual.addEventListener('input',syncPassword);syncPassword();"
    "</script></body></html>";

static const char JOIN_PAGE[] =
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>VibePulse</title><style>"
    "body{font-family:-apple-system,system-ui,sans-serif;background:#0b0b0c;"
    "color:#eaeaea;margin:0;padding:28px 20px;max-width:420px}"
    "h1{font-size:19px;letter-spacing:.08em;text-transform:uppercase}"
    "p{color:#9298a2;font-size:16px;line-height:1.5}"
    "a{display:none;color:#0b0b0c;background:#eaeaea;text-decoration:none;"
    "font-weight:600;padding:13px;border-radius:10px;text-align:center}"
    "</style></head><body><h1>VibePulse</h1>"
    "<p id=\"result\">Connecting&hellip;</p><a id=\"retry\" href=\"/\">"
    "Try again</a><script>"
    "async function check(){try{const r=await fetch('/status',{cache:'no-store'});"
    "const s=await r.json(),p=document.getElementById('result'),"
    "a=document.getElementById('retry');"
    "if(s.state==='connected'){p.textContent='Connected. This Wi-Fi is saved.';}"
    "else if(s.state==='retry'){p.textContent=s.reason==='password'?"
    "'That password did not work.':s.reason==='not-found'?"
    "'Network not found. Check that it has 2.4 GHz Wi-Fi.':"
    "'Could not connect. Please try again.';a.style.display='block';}"
    "}catch(e){}}setInterval(check,750);check();"
    "</script></body></html>";

static esp_err_t page_get(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  httpd_resp_send_chunk(req, PAGE_HEAD, HTTPD_RESP_USE_STRLEN);

  char escaped[TG_WIFI_SSID_CAP * 6 + 1];
  char option[sizeof escaped * 2 + 96];
  for (int i = 0; i < s_scan.n; i++) {
    tg_wifi_html_escape(s_scan.ssid[i], escaped, sizeof escaped);
    int len = snprintf(option, sizeof option,
                       "<option value=\"%s\" data-secured=\"%d\">%s</option>",
                       escaped,
                       authmode_requires_password(s_scan.authmode[i]) ? 1 : 0,
                       escaped);
    if (len > 0) httpd_resp_send_chunk(req, option, (ssize_t)len);
  }
  if (s_scan.n == 0)
    httpd_resp_send_chunk(
        req,
        "<option disabled selected>No 2.4 GHz networks found</option>",
        HTTPD_RESP_USE_STRLEN);
  httpd_resp_send_chunk(req,
      "<option value=\"__manual__\">My network isn't listed</option>",
      HTTPD_RESP_USE_STRLEN);

  httpd_resp_send_chunk(req, PAGE_TAIL, HTTPD_RESP_USE_STRLEN);
  httpd_resp_send_chunk(req, NULL, 0);
  return ESP_OK;
}

static esp_err_t join_post(httpd_req_t *req) {
  /* Taket är generöst mot ett långt SSID + PSK och snålt mot allt annat:
   * kroppen bor på stacken i httpd-tasken. */
  char body[320];
  if (req->content_len <= 0 || req->content_len >= (int)sizeof body) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "body size");
    return ESP_FAIL;
  }
  int got = httpd_req_recv(req, body, (size_t)req->content_len);
  if (got <= 0) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "short body");
    return ESP_FAIL;
  }
  body[got] = '\0';

  char ssid[TG_WIFI_SSID_CAP] = {0};
  char pass[TG_WIFI_PASS_CAP] = {0};
  if (!tg_wifi_form_field(body, "ssid", ssid, sizeof ssid)) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "ssid missing");
    return ESP_FAIL;
  }
  /* Lösenordet får saknas helt — det är ett öppet nät, inte ett fel. */
  if (!tg_wifi_form_field(body, "pass", pass, sizeof pass)) pass[0] = '\0';

  int scan_index = -1;
  for (int i = 0; i < s_scan.n; i++) {
    if (strcmp(s_scan.ssid[i], ssid) == 0) {
      scan_index = i;
      break;
    }
  }
  /* A single scan can miss a real 2.4 GHz AP. Typed names are allowed but
   * treated as secured until a successful connection proves them; only a
   * scanned open/OWE record may omit the password. NVS is written after IP. */
  bool secured = scan_index < 0 ||
      authmode_requires_password(s_scan.authmode[scan_index]);
  if (secured && pass[0] == '\0') {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "password required");
    return ESP_FAIL;
  }
  if (!secured) pass[0] = '\0';

  if (!tg_wifi_ssid_valid(ssid) || !tg_wifi_pass_valid(pass)) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid credentials");
    return ESP_FAIL;
  }
  if (!s_join_lock) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    httpd_resp_sendstr(req, "not ready");
    return ESP_FAIL;
  }

  /* HTTP-tasken publicerar EN atomisk RAM-kopia. Bara vakten rör radion
   * och först efter GOT_IP får kopian skrivas till NVS. */
  xSemaphoreTake(s_join_lock, portMAX_DELAY);
  snprintf(s_join_submission.ssid, sizeof s_join_submission.ssid, "%s", ssid);
  snprintf(s_join_submission.pass, sizeof s_join_submission.pass, "%s", pass);
  s_join_submission.seq++;
  if (s_join_submission.seq == 0) s_join_submission.seq = 1;
  xSemaphoreGive(s_join_lock);
  atomic_store(&s_join_status, TG_WIFI_JOIN_CONNECTING);
  if (s_guard_task) xTaskNotifyGive(s_guard_task);

  httpd_resp_set_type(req, "text/html");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_sendstr(req, JOIN_PAGE);
  ESP_LOGI(TAG, "nya uppgifter för \"%s\" mottagna", ssid);
  return ESP_OK;
}

static esp_err_t status_get(httpd_req_t *req) {
  const char *body = "{\"state\":\"connecting\"}";
  switch (atomic_load(&s_join_status)) {
    case TG_WIFI_JOIN_IDLE:
    case TG_WIFI_JOIN_CONNECTING:
      break;
    case TG_WIFI_JOIN_CONNECTED:
      body = "{\"state\":\"connected\"}";
      break;
    case TG_WIFI_JOIN_RETRY_PASSWORD:
      body = "{\"state\":\"retry\",\"reason\":\"password\"}";
      break;
    case TG_WIFI_JOIN_RETRY_NOT_FOUND:
      body = "{\"state\":\"retry\",\"reason\":\"not-found\"}";
      break;
    case TG_WIFI_JOIN_RETRY_CONNECTION:
      body = "{\"state\":\"retry\",\"reason\":\"connection\"}";
      break;
  }
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_sendstr(req, body);
  return ESP_OK;
}

/* Allt annat pekas tillbaka till sidan. Det är det som får iOS och Android
 * att öppna portalen av sig själva i stället för att bara varna om att
 * nätet saknar internet. */
static esp_err_t catch_all_get(httpd_req_t *req) {
  httpd_resp_set_status(req, "302 Found");
  httpd_resp_set_hdr(req, "Location", "http://" AP_ADDRESS "/");
  httpd_resp_send(req, NULL, 0);
  return ESP_OK;
}

/* ------------------------------------------------------------------- dns */

/*
 * En DNS-lögnare som svarar 192.168.4.1 på ALLT. Bara medan fönstret är
 * öppet, bara på accesspunktens eget nät — den lämnar aldrig glasets
 * räckvidd, och utan den blir portalen en IP-adress man måste skriva av
 * från skärmen.
 */
static void dns_task(void *arg) {
  (void)arg;
  int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (sock < 0) {
    ESP_LOGW(TAG, "ingen DNS-socket — portalen nås via " AP_ADDRESS);
    vTaskDelete(NULL);
    return;
  }
  struct sockaddr_in addr = {
    .sin_family = AF_INET,
    .sin_port = htons(53),
    .sin_addr.s_addr = htonl(INADDR_ANY),
  };
  struct timeval tv = { .tv_sec = 1, .tv_usec = 0 };
  setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

  if (bind(sock, (struct sockaddr *)&addr, sizeof addr) < 0) {
    ESP_LOGW(TAG, "DNS-porten kunde inte tas — portalen nås via " AP_ADDRESS);
    close(sock);
    vTaskDelete(NULL);
    return;
  }

  uint8_t pkt[256];
  while (!atomic_load(&s_dns_stop)) {
    struct sockaddr_in from;
    socklen_t fromlen = sizeof from;
    int n = recvfrom(sock, pkt, sizeof pkt, 0, (struct sockaddr *)&from,
                     &fromlen);
    if (n < 12) continue; /* timeout eller för kort för ett DNS-huvud */

    /* Bara vanliga frågor med exakt en fråga besvaras. Allt annat
     * ignoreras hellre än gissas på. */
    if ((pkt[2] & 0x80) != 0) continue;          /* redan ett svar */
    if (pkt[4] != 0 || pkt[5] != 1) continue;    /* QDCOUNT != 1   */

    /* Hoppa QNAME: längdprefixade etiketter fram till nollbyten. */
    int p = 12;
    while (p < n && pkt[p] != 0) {
      if ((pkt[p] & 0xC0) != 0) { p = n; break; } /* pekare i en fråga */
      p += pkt[p] + 1;
    }
    if (p >= n - 4) continue;
    int qend = p + 1 + 4; /* nollbyte + QTYPE + QCLASS */
    if (qend > n || qend + 16 > (int)sizeof pkt) continue;

    pkt[2] = 0x84; /* QR=1, AA=1 */
    pkt[3] = 0x00; /* ingen rekursion, ingen felkod */
    pkt[6] = 0x00; pkt[7] = 0x01; /* ANCOUNT = 1 */
    pkt[8] = 0x00; pkt[9] = 0x00; /* NSCOUNT = 0 */
    pkt[10] = 0x00; pkt[11] = 0x00; /* ARCOUNT = 0 */

    uint8_t *a = pkt + qend;
    a[0] = 0xC0; a[1] = 0x0C;             /* pekare till frågans namn */
    a[2] = 0x00; a[3] = 0x01;             /* TYPE A                   */
    a[4] = 0x00; a[5] = 0x01;             /* CLASS IN                 */
    a[6] = 0; a[7] = 0; a[8] = 0; a[9] = 60; /* TTL 60 s              */
    a[10] = 0x00; a[11] = 0x04;           /* RDLENGTH 4               */
    a[12] = 192; a[13] = 168; a[14] = 4; a[15] = 1;

    sendto(sock, pkt, qend + 16, 0, (struct sockaddr *)&from, fromlen);
  }
  close(sock);
  vTaskDelete(NULL);
}

/* --------------------------------------------------------------- fönstret */

static void server_start(void) {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.lru_purge_enable = true;
  cfg.max_uri_handlers = 4;
  cfg.uri_match_fn = httpd_uri_match_wildcard;
  cfg.stack_size = 4096;

  if (httpd_start(&s_server, &cfg) != ESP_OK) {
    ESP_LOGE(TAG, "http-servern startade inte — glaset visar ändå nätet");
    s_server = NULL;
    return;
  }
  static const httpd_uri_t root = {
    .uri = "/", .method = HTTP_GET, .handler = page_get };
  static const httpd_uri_t join = {
    .uri = "/join", .method = HTTP_POST, .handler = join_post };
  static const httpd_uri_t status = {
    .uri = "/status", .method = HTTP_GET, .handler = status_get };
  static const httpd_uri_t rest = {
    .uri = "/*", .method = HTTP_GET, .handler = catch_all_get };
  httpd_register_uri_handler(s_server, &root);
  httpd_register_uri_handler(s_server, &join);
  httpd_register_uri_handler(s_server, &status);
  httpd_register_uri_handler(s_server, &rest);
}

/* DMA-läget i loggen vid varje steg av öppningen: accesspunkten är den
 * enda ytan i firmwaren vars minneskostnad aldrig mätts på hårdvara, och
 * kilningen 2026-08-17 (första fysiska körningen) obducerades i blindo.
 * Nästa incident ska peka ut exakt vilket steg som åt blocket. */
static size_t dma_log(const char *stage) {
  size_t largest = heap_caps_get_largest_free_block(MALLOC_CAP_DMA);
  ESP_LOGI(TAG, "DMA största block %s: %u byte", stage, (unsigned)largest);
  return largest;
}

static void window_close(void);

static void window_open(void) {
  /* GRIND 1, före allt: ryms accesspunkten utan att närma sig flushens
   * DMA-tak? Ett vägrat fönster är en loggrad och ett nytt försök om en
   * stund; en fryst panel är en USB-räddning. */
  size_t largest = dma_log("före öppning");
  if (!tg_wifi_setup_dma_ok_to_open(largest, s_hooks->flush_dma_bytes)) {
    ESP_LOGW(TAG, "setupfönstret VÄGRAR öppna: DMA-blocket %u byte < "
             "%d x flushens %u + %u reserv — hellre stängt än fryst glas",
             (unsigned)largest, TG_WIFI_SETUP_DMA_OPEN_FACTOR,
             (unsigned)s_hooks->flush_dma_bytes,
             (unsigned)TG_WIFI_SETUP_DMA_OPEN_RESERVE_BYTES);
    return;
  }

  /* Port 80 kan bara ha en ägare. Ett OTA-fönster utan nät kan ändå inte
   * ta emot en uppladdning, så nätlagret vinner den konflikten — och
   * säger det i loggen i stället för att tyst misslyckas. */
  if (torget_ota_service_maintenance_open()) {
    ESP_LOGI(TAG, "stänger OTA-fönstret: porten och minnet behövs här");
    torget_ota_service_close_maintenance();
    vTaskDelay(pdMS_TO_TICKS(1200)); /* låt OTA-vakten lämna tillbaka porten */
    dma_log("efter OTA-stopp");
  }

  if (s_hooks->sta_pause) s_hooks->sta_pause(true);
  esp_wifi_disconnect();
  vTaskDelay(pdMS_TO_TICKS(200));

  scan_networks();
  derive_ap_password();
  dma_log("efter skanning");

  if (!s_ap_netif) s_ap_netif = esp_netif_create_default_wifi_ap();

  wifi_config_t ap = {0};
  snprintf((char *)ap.ap.ssid, sizeof ap.ap.ssid, "%s", AP_SSID);
  ap.ap.ssid_len = (uint8_t)strlen(AP_SSID);
  snprintf((char *)ap.ap.password, sizeof ap.ap.password, "%s", s_ap_pass);
  ap.ap.channel = AP_CHANNEL;
  ap.ap.max_connection = AP_MAX_CONN;
  ap.ap.authmode = WIFI_AUTH_WPA2_PSK;

  esp_err_t err = esp_wifi_set_mode(WIFI_MODE_APSTA);
  if (err == ESP_OK) err = esp_wifi_set_config(WIFI_IF_AP, &ap);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "accesspunkten kom inte upp: %s", esp_err_to_name(err));
    esp_wifi_set_mode(WIFI_MODE_STA);
    if (s_hooks->sta_pause) s_hooks->sta_pause(false);
    return;
  }

  /* GRIND 2, efter APSTA-bytet — den dyra biten. Föll blocket under x2 av
   * flushen är NÄSTA flush i farozonen: riv hellre fönstret medan glaset
   * fortfarande lever än fortsätt stapla httpd + DNS ovanpå. */
  largest = dma_log("efter APSTA");
  if (!tg_wifi_setup_dma_ok_to_continue(largest, s_hooks->flush_dma_bytes)) {
    ESP_LOGE(TAG, "setupfönstret AVBRYTER: DMA-blocket %u byte < "
             "%d x flushens %u efter AP-start — river innan glaset fryser",
             (unsigned)largest, TG_WIFI_SETUP_DMA_ABORT_FACTOR,
             (unsigned)s_hooks->flush_dma_bytes);
    esp_wifi_set_mode(WIFI_MODE_STA);
    if (s_hooks->sta_pause) s_hooks->sta_pause(false);
    return;
  }

  atomic_store(&s_dns_stop, false);
  if (xTaskCreate(dns_task, "tg-wifi-dns", 3072, NULL, 4, NULL) != pdPASS)
    ESP_LOGW(TAG, "DNS-lögnaren startade inte — portalen nås via " AP_ADDRESS);
  server_start();

  /* GRIND 3, efter den verkliga portalallokeringen. Startreserven ovan ska
   * bära DNS- och httpd-taskarna, men mät utfallet i stället för att lita
   * på uppskattningen. Publicera aldrig ett setupfönster som redan har ätit
   * upp displayens etablerade x2-marginal. */
  largest = dma_log("efter portalstart");
  if (!tg_wifi_setup_dma_ok_to_continue(largest, s_hooks->flush_dma_bytes)) {
    ESP_LOGE(TAG, "setupfönstret AVBRYTER: DMA-blocket %u byte < "
             "%d x flushens %u efter portalstart — river innan glaset fryser",
             (unsigned)largest, TG_WIFI_SETUP_DMA_ABORT_FACTOR,
             (unsigned)s_hooks->flush_dma_bytes);
    window_close();
    return;
  }

  atomic_store(&s_open, true);
  /* Lösenordet står på glaset, inte i loggen: den som läser serieloggen
   * har redan kabeln, men loggen kan hamna i en bugrapport. */
  ESP_LOGI(TAG, "setupfönstret öppet i tio minuter (%s)", AP_SSID);
}

static void window_close(void) {
  if (s_server) {
    httpd_stop(s_server);
    s_server = NULL;
  }
  atomic_store(&s_dns_stop, true);
  /* DNS-tasken vaknar ur sin sekundtimeout, ser flaggan och tar bort sig
   * själv; sockeln stängs där, inte här. */
  vTaskDelay(pdMS_TO_TICKS(1200));

  if (s_hooks->credentials_abandoned) s_hooks->credentials_abandoned();
  esp_wifi_set_mode(WIFI_MODE_STA);
  atomic_store(&s_open, false);
  join_submission_clear();
  if (s_hooks->sta_pause) s_hooks->sta_pause(false);
  ESP_LOGI(TAG, "setupfönstret stängt");
}

/* ----------------------------------------------------------------- vakten */

static void guard_task(void *arg) {
  (void)arg;
  int64_t no_ip_since = esp_timer_get_time();
  int64_t opened_us = 0;
  int64_t last_close_us = 0;
  int64_t got_ip_us = 0;
  tg_wifi_slot active_trial = {0};
  uint32_t applied_seq = 0;

  for (;;) {
    /* Ett KEY3-håll väcker oss direkt. Timeouten behåller den vanliga
     * nät-/autoöppningsvakten utan en separat timer eller busy-loop. */
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));

    const int64_t now = esp_timer_get_time();
    const bool have_ip = s_hooks->have_ip && s_hooks->have_ip();
    const bool open = atomic_load(&s_open);
    tg_wifi_setup_phase phase = atomic_load(&s_phase);

    if (have_ip) {
      /* Flanken false->true är den enda gången listan behöver skrivas om;
       * att göra det varje halvsekund vore rent flashslitage. */
      if (no_ip_since != 0 && s_hooks->ip_acquired) s_hooks->ip_acquired();
      no_ip_since = 0;
    } else if (no_ip_since == 0) {
      no_ip_since = now;
      got_ip_us = 0;
    }

    if (!open) {
      if (phase == TG_WIFI_PHASE_FAILED &&
          atomic_exchange(&s_want_close, false)) {
        atomic_store(&s_phase, TG_WIFI_PHASE_IDLE);
        torget_wifi_ui_set(TG_WIFI_UI_HIDDEN, NULL, NULL, NULL, 0);
        last_close_us = now;
        continue;
      }
      bool asked = atomic_exchange(&s_want_open, false);
      if (phase != TG_WIFI_PHASE_FAILED)
        atomic_store(&s_want_close, false);
      bool automatic = phase == TG_WIFI_PHASE_IDLE &&
          tg_wifi_setup_should_open(have_ip, now, no_ip_since, last_close_us);
      if (asked || automatic) {
        opened_us = now;
        got_ip_us = 0;
        applied_seq = 0;
        memset(&active_trial, 0, sizeof active_trial);
        atomic_store(&s_phase, TG_WIFI_PHASE_STARTING);
        torget_wifi_ui_set(TG_WIFI_UI_STARTING, NULL, NULL, NULL, 0);
        window_open();
        if (!atomic_load(&s_open)) { /* öppningen föll — försök inte i loop */
          opened_us = 0;
          last_close_us = now;
          atomic_store(&s_phase, TG_WIFI_PHASE_FAILED);
        } else {
          atomic_store(&s_phase, TG_WIFI_PHASE_OPEN);
        }
      }
    } else {
      /* POST-tasken får bara publicera RAM. Vakten kopierar en hel version
       * under lås och provar varje version exakt en gång. */
      uint32_t submitted_seq = 0;
      bool applied_now = false;
      if (s_join_lock) {
        xSemaphoreTake(s_join_lock, portMAX_DELAY);
        submitted_seq = s_join_submission.seq;
        if (tg_wifi_join_should_apply(submitted_seq, applied_seq)) {
          snprintf(active_trial.ssid, sizeof active_trial.ssid, "%s",
                   s_join_submission.ssid);
          snprintf(active_trial.pass, sizeof active_trial.pass, "%s",
                   s_join_submission.pass);
          active_trial.seq = submitted_seq;
        }
        xSemaphoreGive(s_join_lock);
      }

      if (tg_wifi_join_should_apply(submitted_seq, applied_seq)) {
        applied_now = true;
        applied_seq = submitted_seq;
        got_ip_us = 0;
        atomic_store(&s_join_status, TG_WIFI_JOIN_CONNECTING);
        atomic_store(&s_phase, TG_WIFI_PHASE_JOINING);
        if (!s_hooks->try_credentials ||
            !s_hooks->try_credentials(active_trial.ssid, active_trial.pass)) {
          atomic_store(&s_join_status, TG_WIFI_JOIN_RETRY_CONNECTION);
        }
      }

      tg_wifi_join_status join_status = atomic_load(&s_join_status);
      if (join_status == TG_WIFI_JOIN_CONNECTING) {
        if (!applied_now && have_ip) {
          /* Detta är den enda skrivvägen: fungerande IP först, NVS sedan. */
          if (tg_wifi_creds_remember(active_trial.ssid, active_trial.pass)) {
            got_ip_us = now;
            memset(active_trial.pass, 0, sizeof active_trial.pass);
            atomic_store(&s_join_status, TG_WIFI_JOIN_CONNECTED);
            atomic_store(&s_phase, TG_WIFI_PHASE_JOINED);
            if (s_hooks->credentials_accepted)
              s_hooks->credentials_accepted(active_trial.ssid);
          } else {
            atomic_store(&s_join_status, TG_WIFI_JOIN_RETRY_CONNECTION);
            if (s_hooks->credentials_abandoned)
              s_hooks->credentials_abandoned();
          }
        } else if (s_hooks->last_disconnect_reason) {
          int reason = s_hooks->last_disconnect_reason();
          if (reason != 0)
            atomic_store(&s_join_status, tg_wifi_disconnect_status(reason));
        }
      }

      if (atomic_exchange(&s_want_close, false) ||
          tg_wifi_setup_should_close(now, opened_us, got_ip_us)) {
        window_close();
        memset(&active_trial, 0, sizeof active_trial);
        applied_seq = 0;
        atomic_store(&s_phase, TG_WIFI_PHASE_IDLE);
        last_close_us = now;
        opened_us = 0;
      }
    }

    /* Glaset: nätlagret syns bara när det finns något att säga. En panel
     * med IP har inget ärende här — då äger apparna skärmen.
     *
     * OTA-overlayn har alltid företräde: efter en OTA-omstart utan nät är
     * BÅDE underhållsfönstret (återväpnat av PENDING_VERIFY-booten) och
     * nätsökningen aktiva, och båda lagren flyttar sig främst vid varje
     * uppdatering — utan den här spärren flimrar de om förgrunden en gång
     * i sekunden. Ringen vinner; nätsidan väntar tills fönstret stängt.
     * Setupfönstret är undantaget: window_open() stängde OTA-fönstret för
     * port 80, så konflikten kan inte uppstå där. */
    const bool now_open = atomic_load(&s_open);
    phase = atomic_load(&s_phase);
    if (phase == TG_WIFI_PHASE_STARTING) {
      torget_wifi_ui_set(TG_WIFI_UI_STARTING, NULL, NULL, NULL, 0);
    } else if (phase == TG_WIFI_PHASE_FAILED) {
      torget_wifi_ui_set(TG_WIFI_UI_FAILED, NULL, NULL,
                         "TRY AGAIN WITH KEY3", 0);
    } else if (!now_open && torget_ota_service_maintenance_open()) {
      torget_wifi_ui_set(TG_WIFI_UI_HIDDEN, NULL, NULL, NULL, 0);
    } else if (have_ip && !now_open) {
      torget_wifi_ui_set(TG_WIFI_UI_HIDDEN, NULL, NULL, NULL, 0);
    } else if (now_open && applied_seq != 0) {
      tg_wifi_join_status status = atomic_load(&s_join_status);
      tg_wifi_ui_state ui_state = TG_WIFI_UI_JOINING;
      const char *detail = NULL;
      if (status == TG_WIFI_JOIN_CONNECTED) {
        ui_state = TG_WIFI_UI_JOINED;
      } else if (status == TG_WIFI_JOIN_RETRY_PASSWORD) {
        ui_state = TG_WIFI_UI_FAILED;
        detail = "WRONG PASSWORD - TRY AGAIN ON PHONE";
      } else if (status == TG_WIFI_JOIN_RETRY_NOT_FOUND) {
        ui_state = TG_WIFI_UI_FAILED;
        detail = "NOT FOUND - 2.4 GHZ ONLY";
      } else if (status == TG_WIFI_JOIN_RETRY_CONNECTION) {
        ui_state = TG_WIFI_UI_FAILED;
        detail = "COULD NOT CONNECT - TRY AGAIN";
      }
      torget_wifi_ui_set(ui_state, active_trial.ssid, NULL, detail, 0);
    } else if (now_open) {
      int left = (int)((TG_WIFI_SETUP_WINDOW_US - (now - opened_us)) / 1000000);
      torget_wifi_ui_set(TG_WIFI_UI_OPEN, AP_SSID, s_ap_pass, NULL, left);
    } else if (tg_wifi_search_ui_visible(have_ip, now, no_ip_since)) {
      /* Den ärliga nätsidan: vilket nät jagas, vad radion svarade, och hur
       * länge det är kvar tills fönstret öppnar sig självt. Först efter
       * TG_WIFI_SEARCH_UI_US — bootskärmen äger uppstarten. */
      const char *ssid = s_hooks->current_ssid ? s_hooks->current_ssid() : NULL;
      const char *reason = s_hooks->last_reason ? s_hooks->last_reason() : NULL;
      int64_t until = TG_WIFI_SETUP_AUTO_US - (now - no_ip_since);
      int left = until > 0 ? (int)(until / 1000000) : 0;
      torget_wifi_ui_set(TG_WIFI_UI_SEARCHING, ssid, NULL, reason, left);
    } else {
      /* Kort svacka eller pågående boot: apparna behåller glaset. */
      torget_wifi_ui_set(TG_WIFI_UI_HIDDEN, NULL, NULL, NULL, 0);
    }
  }
}

void torget_wifi_setup_start(const tg_wifi_setup_hooks *hooks) {
  if (!hooks) return;
  s_hooks = hooks;
  ESP_LOGI(TAG, "%d ihågkomna nät i NVS", tg_wifi_creds_count());
  if (!s_join_lock) s_join_lock = xSemaphoreCreateMutex();
  if (!s_join_lock) {
    ESP_LOGE(TAG, "setupvakten saknar credential-lås — nya nät kräver USB");
    return;
  }
  join_submission_clear();
  /* 5 kB, inte 4: vakten är den som får röra NVS åt värdlagret, och en
   * hel slotlista är 600 byte på stacken innan NVS ens börjat. */
  s_guard_task = NULL;
  if (xTaskCreate(guard_task, "tg-wifi-setup", 5120, NULL, 4,
                  &s_guard_task) != pdPASS) {
    s_guard_task = NULL;
    ESP_LOGE(TAG, "setupvakten kunde inte skapas — nya nät kräver USB");
  }
}

void torget_wifi_setup_request_open(void) {
  if (!s_guard_task) return;
  tg_wifi_setup_phase phase = atomic_load(&s_phase);
  while (phase == TG_WIFI_PHASE_IDLE || phase == TG_WIFI_PHASE_FAILED) {
    if (atomic_compare_exchange_weak(&s_phase, &phase,
                                     TG_WIFI_PHASE_STARTING)) {
      atomic_store(&s_want_close, false);
      atomic_store(&s_want_open, true);
      if (s_guard_task) xTaskNotifyGive(s_guard_task);
      return;
    }
  }
}

void torget_wifi_setup_request_close(void) {
  tg_wifi_setup_phase phase = atomic_load(&s_phase);
  if (!tg_wifi_setup_can_close(phase)) return;
  atomic_store(&s_want_close, true);
  if (s_guard_task) xTaskNotifyGive(s_guard_task);
}

bool torget_wifi_setup_is_open(void) { return atomic_load(&s_open); }
bool torget_wifi_setup_owns_input(void) {
  return tg_wifi_setup_owns_input(atomic_load(&s_phase));
}
