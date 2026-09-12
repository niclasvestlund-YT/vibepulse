#include "service_discovery.h"
#include "service_discovery_policy.h"

#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "lwip/ip4_addr.h"
#include "mdns.h"
#include "nvs.h"

#define TG_DISCOVERY_ORIGIN_CAP 64
#define TG_DISCOVERY_QUERY_MS 1500
#define TG_DISCOVERY_RESULTS 8
#define TG_DISCOVERY_RETRY_US (10LL * 1000000LL)
#define TG_DISCOVERY_FAILED_US (30LL * 1000000LL)
#define TG_DISCOVERY_FAILED_CAP 4

static const char *TAG = "service-discovery";
static const char *NVS_NAMESPACE = "vibepulse";
static const char *NVS_ORIGIN_KEY = "service_lkg";

typedef struct {
  char origin[TG_DISCOVERY_ORIGIN_CAP];
  int64_t retry_after_us;
} failed_origin;

static StaticSemaphore_t s_lock_storage;
static SemaphoreHandle_t s_lock;
static portMUX_TYPE s_init_lock = portMUX_INITIALIZER_UNLOCKED;
static bool s_loaded;
static bool s_querying;
static bool s_mdns_ready;
static int64_t s_next_query_us;
static char s_active_origin[TG_DISCOVERY_ORIGIN_CAP];
static char s_persisted_origin[TG_DISCOVERY_ORIGIN_CAP];
static failed_origin s_failed[TG_DISCOVERY_FAILED_CAP];
static unsigned s_failed_next;

static bool ensure_lock(void) {
  if (s_lock != NULL) return true;
  taskENTER_CRITICAL(&s_init_lock);
  if (s_lock == NULL) s_lock = xSemaphoreCreateMutexStatic(&s_lock_storage);
  taskEXIT_CRITICAL(&s_init_lock);
  return s_lock != NULL;
}

static bool bounded_copy(char *dst, size_t cap, const char *src) {
  if (!dst || cap == 0 || !src) return false;
  int written = snprintf(dst, cap, "%s", src);
  return written >= 0 && (size_t)written < cap;
}

static void load_lkg_locked(void) {
  if (s_loaded) return;
  s_loaded = true;
  nvs_handle_t handle;
  if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) return;
  size_t len = sizeof s_active_origin;
  esp_err_t err = nvs_get_str(handle, NVS_ORIGIN_KEY, s_active_origin, &len);
  nvs_close(handle);
  if (err != ESP_OK || !tg_service_origin_valid(s_active_origin)) {
    s_active_origin[0] = '\0';
    s_persisted_origin[0] = '\0';
  } else {
    bounded_copy(s_persisted_origin, sizeof s_persisted_origin,
                 s_active_origin);
  }
}

static void save_lkg(const char *origin) {
  nvs_handle_t handle;
  if (!origin || nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK)
    return;
  esp_err_t err = nvs_set_str(handle, NVS_ORIGIN_KEY, origin);
  if (err == ESP_OK) err = nvs_commit(handle);
  nvs_close(handle);
  if (err != ESP_OK) ESP_LOGW(TAG, "kunde inte spara senast fungerande värd");
}

static bool version_one(const mdns_result_t *result) {
  if (!result) return false;
  for (size_t i = 0; i < result->txt_count; ++i) {
    if (result->txt[i].key && result->txt[i].value &&
        strcmp(result->txt[i].key, "v") == 0 &&
        strcmp(result->txt[i].value, "1") == 0) {
      return true;
    }
  }
  return false;
}

static bool failed_now_locked(const char *origin, int64_t now_us) {
  for (size_t i = 0; i < TG_DISCOVERY_FAILED_CAP; ++i) {
    if (s_failed[i].origin[0] &&
        strcmp(s_failed[i].origin, origin) == 0 &&
        now_us < s_failed[i].retry_after_us) {
      return true;
    }
  }
  return false;
}

static bool select_result(mdns_result_t *results, char *origin, size_t cap,
                          int64_t now_us) {
  for (mdns_result_t *result = results; result; result = result->next) {
    if (!version_one(result) || result->port == 0) continue;
    for (mdns_ip_addr_t *addr = result->addr; addr; addr = addr->next) {
      if (addr->addr.type != ESP_IPADDR_TYPE_V4) continue;
      char candidate[TG_DISCOVERY_ORIGIN_CAP];
      int written = snprintf(candidate, sizeof candidate, "http://" IPSTR ":%u",
                             IP2STR(&addr->addr.u_addr.ip4),
                             (unsigned)result->port);
      if (written < 0 || (size_t)written >= sizeof candidate) continue;
      if (xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) return false;
      bool failed = failed_now_locked(candidate, now_us);
      xSemaphoreGive(s_lock);
      if (!failed) return bounded_copy(origin, cap, candidate);
    }
  }
  return false;
}

static bool query_service(char *origin, size_t cap, int64_t now_us) {
  if (!s_mdns_ready) {
    esp_err_t err = mdns_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return false;
    s_mdns_ready = true;
  }
  mdns_result_t *results = NULL;
  esp_err_t err = mdns_query_ptr("_vibepulse", "_tcp",
                                 TG_DISCOVERY_QUERY_MS,
                                 TG_DISCOVERY_RESULTS, &results);
  bool found = err == ESP_OK && select_result(results, origin, cap, now_us);
  if (results) mdns_query_results_free(results);
  return found;
}

bool torget_service_endpoint_url(const char *path, const char *configured_url,
                                 char *url, size_t cap,
                                 tg_service_source *source) {
  if (source) *source = TG_SERVICE_SOURCE_CONFIGURED;
  if (!path || !url || cap == 0 || !ensure_lock()) return false;

  const int64_t now_us = esp_timer_get_time();
  bool should_query = false;
  char active[TG_DISCOVERY_ORIGIN_CAP] = {0};
  if (xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) return false;
  load_lkg_locked();
  if (s_active_origin[0]) {
    bounded_copy(active, sizeof active, s_active_origin);
  } else if (!s_querying && now_us >= s_next_query_us) {
    s_querying = true;
    should_query = true;
  }
  xSemaphoreGive(s_lock);

  if (should_query) {
    char discovered[TG_DISCOVERY_ORIGIN_CAP] = {0};
    bool found = query_service(discovered, sizeof discovered, now_us);
    if (xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
      s_querying = false;
      s_next_query_us = now_us + TG_DISCOVERY_RETRY_US;
      if (found) {
        bounded_copy(s_active_origin, sizeof s_active_origin, discovered);
        bounded_copy(active, sizeof active, discovered);
      }
      xSemaphoreGive(s_lock);
    }
  }

  if (active[0] && tg_service_build_endpoint(active, path, url, cap)) {
    if (source) *source = TG_SERVICE_SOURCE_DISCOVERED;
    return true;
  }
  /* No compiled-in fallback and nothing advertised: fail closed, so the
   * caller logs a miss rather than chasing a NULL. */
  return configured_url && bounded_copy(url, cap, configured_url);
}

void torget_service_note_result(tg_service_source source, const char *url,
                                bool ok) {
  if (source != TG_SERVICE_SOURCE_DISCOVERED || !url || !ensure_lock()) return;
  const char *path = strstr(url + 7, "/");
  if (!path) return;
  size_t len = (size_t)(path - url);
  if (len == 0 || len >= TG_DISCOVERY_ORIGIN_CAP) return;
  char origin[TG_DISCOVERY_ORIGIN_CAP];
  memcpy(origin, url, len);
  origin[len] = '\0';

  bool persist = false;
  const int64_t now_us = esp_timer_get_time();
  if (xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) return;
  load_lkg_locked();
  if (ok) {
    persist = strcmp(s_persisted_origin, origin) != 0;
    bounded_copy(s_active_origin, sizeof s_active_origin, origin);
    if (persist) {
      bounded_copy(s_persisted_origin, sizeof s_persisted_origin, origin);
    }
  } else {
    failed_origin *failed = &s_failed[s_failed_next];
    s_failed_next = (s_failed_next + 1u) % TG_DISCOVERY_FAILED_CAP;
    bounded_copy(failed->origin, sizeof failed->origin, origin);
    failed->retry_after_us = now_us + TG_DISCOVERY_FAILED_US;
    if (strcmp(s_active_origin, origin) == 0) s_active_origin[0] = '\0';
    s_next_query_us = now_us;
  }
  xSemaphoreGive(s_lock);
  if (ok && persist) save_lkg(origin);
}
