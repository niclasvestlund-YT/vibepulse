/* 7B-C transport adapter. No voice credentials or Wi-Fi secrets in firmware source. */
#include "voice_device.h"
#include "grok_ui.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "esp_codec_dev.h"
#include "bsp/esp-bsp.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "esp_lv_adapter.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "cJSON.h"

#define CONNECTED BIT0
#define PCM_BYTES 3200 /* 100 ms, mono s16le, 16 kHz */
static EventGroupHandle_t wifi_events;
static QueueHandle_t requests;
static char base_url[256], bearer[192], device[65];
static esp_codec_dev_handle_t microphone, speaker;
static bool configured;
static bool codecs_ready;
typedef struct { int result; int16_t pcm[PCM_BYTES / 2]; } audio_frame_t;
static QueueHandle_t capture_queue;
static EventGroupHandle_t capture_events;
static atomic_bool capture_stop;

static void capture_worker(void *arg) {
    (void)arg;
    audio_frame_t frame;
    int16_t stereo[PCM_BYTES];
    while (!atomic_load(&capture_stop)) {
        frame.result = esp_codec_dev_read(microphone, stereo, sizeof(stereo));
        if (frame.result != ESP_OK) {
            xQueueSend(capture_queue, &frame, 0);
            break;
        }
        /* Vendor example uses two I2S channels. Average into network mono. */
        for (int i = 0; i < PCM_BYTES / 2; ++i)
            frame.pcm[i] = ((int32_t)stereo[2*i] + stereo[2*i+1]) / 2;
        /* A bounded 800 ms queue decouples continuous I2S capture from HTTP.
         * Never drop samples silently: a slow network aborts this utterance. */
        if (xQueueSend(capture_queue, &frame, pdMS_TO_TICKS(100)) != pdTRUE) break;
    }
    xEventGroupSetBits(capture_events, BIT0);
    vTaskDelete(NULL);
}

static void show(const char *state, const char *transcript, const char *reply) {
    if (esp_lv_adapter_lock(1000) == ESP_OK) {
        grok_ui_voice_update(state, transcript, reply);
        esp_lv_adapter_unlock();
    }
}

static bool nvs_string(nvs_handle_t nvs, const char *key, char *out, size_t size) {
    return nvs_get_str(nvs, key, out, &size) == ESP_OK && out[0];
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)data;
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_events, CONNECTED);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_events, CONNECTED);
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    }
}

static esp_http_client_handle_t client(const char *path, esp_http_client_method_t method) {
    char url[384];
    if (snprintf(url, sizeof(url), "%s%s", base_url, path) >= sizeof(url)) return NULL;
    esp_http_client_config_t cfg = {.url = url, .method = method, .timeout_ms = 4000,
        .disable_auto_redirect = true, .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 4096};
    esp_http_client_handle_t h = esp_http_client_init(&cfg);
    if (h) esp_http_client_set_header(h, "Authorization", bearer);
    return h;
}

static cJSON *exchange(const char *path, const void *body, int length, bool pcm) {
    esp_http_client_handle_t h = client(path, body ? HTTP_METHOD_POST : HTTP_METHOD_GET);
    if (!h) return NULL;
    esp_http_client_set_header(h, "Content-Type", pcm ? "application/octet-stream" : "application/json");
    cJSON *result = NULL;
    if (esp_http_client_open(h, length) != ESP_OK) goto done;
    if (body && esp_http_client_write(h, body, length) != length) goto done;
    int64_t bytes = esp_http_client_fetch_headers(h);
    if (esp_http_client_get_status_code(h) != 200 || bytes <= 0 || bytes > 32768) goto done;
    char *json = malloc((size_t)bytes + 1);
    if (!json) goto done;
    int total = 0;
    while (total < bytes) {
        int got = esp_http_client_read(h, json + total, (int)bytes - total);
        if (got <= 0) break;
        total += got;
    }
    json[total] = 0;
    if (total == bytes) result = cJSON_Parse(json);
    free(json);
done:
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return result;
}

static const char *field(cJSON *json, const char *key) {
    cJSON *value = cJSON_GetObjectItemCaseSensitive(json, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}

static bool playback(const char *sid, int version) {
    char path[100];
    snprintf(path, sizeof(path), "/grokbot/playback/%s/%d", sid, version);
    esp_http_client_handle_t h = client(path, HTTP_METHOD_GET);
    if (!h) return false;
    bool ok = false;
    if (esp_http_client_open(h, 0) != ESP_OK) goto done;
    int64_t length = esp_http_client_fetch_headers(h);
    if (esp_http_client_get_status_code(h) != 200 || length <= 0 || length > 16000 * 2 * 120 || length % 2) goto done;
    uint8_t pcm[2048];
    int16_t stereo[2048];
    int total = 0, pending = 0;
    int64_t deadline = esp_timer_get_time() + 130000000LL;
    while (total < length && esp_timer_get_time() < deadline) {
        int n = esp_http_client_read(h, (char *)pcm + pending, sizeof(pcm) - pending);
        if (n <= 0) goto done;
        total += n;
        n += pending;
        int aligned = n & ~1;
        for (int i = 0; i < aligned / 2; ++i) {
            int16_t sample = (int16_t)((uint16_t)pcm[2*i] | ((uint16_t)pcm[2*i+1] << 8));
            stereo[2*i] = sample;
            stereo[2*i+1] = sample;
        }
        if (aligned && esp_codec_dev_write(speaker, stereo, aligned * 2) != ESP_OK) goto done;
        pending = n - aligned;
        if (pending) pcm[0] = pcm[aligned];
    }
    ok = total == length && pending == 0;
done:
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return ok;
}

static bool codecs(void) {
    if (codecs_ready) return true;
    if (!speaker) speaker = bsp_audio_codec_speaker_init();
    if (!microphone) microphone = bsp_audio_codec_microphone_init();
    if (!speaker || !microphone) return false;
    esp_codec_dev_sample_info_t format = {.sample_rate = 16000, .channel = 2, .bits_per_sample = 16};
    if (esp_codec_dev_open(speaker, &format) != ESP_OK) return false;
    if (esp_codec_dev_set_out_vol(speaker, 45) != ESP_OK) return false;
    /* Microphone is opened only after a tap, never at boot or during playback. */
    codecs_ready = true;
    return true;
}

static void session(void) {
    char path[100], sid[37] = {0};
    cJSON *payload = cJSON_CreateObject();
    cJSON_AddStringToObject(payload, "device", device);
    char *body = cJSON_PrintUnformatted(payload);
    cJSON_Delete(payload);
    if (!body) return;
    cJSON *response = exchange("/grokbot/session", body, strlen(body), false);
    cJSON_free(body);
    if (!response || strlen(field(response, "request_id")) != 36) goto fail;
    snprintf(sid, sizeof(sid), "%s", field(response, "request_id"));
    cJSON_Delete(response); response = NULL;
    esp_codec_dev_sample_info_t format = {.sample_rate = 16000, .channel = 2, .bits_per_sample = 16};
    if (esp_codec_dev_open(microphone, &format) != ESP_OK) goto fail;
    esp_codec_dev_set_in_gain(microphone, 24);
    show("Listening", "", "");
    audio_frame_t frame;
    xQueueReset(capture_queue);
    xEventGroupClearBits(capture_events, BIT0);
    atomic_store(&capture_stop, false);
    if (xTaskCreate(capture_worker, "grok_capture", 12288, NULL, 5, NULL) != pdPASS) goto fail;
    int64_t capture_deadline = esp_timer_get_time() + 35000000LL;
    for (int seq = 0; seq < 300 && esp_timer_get_time() < capture_deadline; seq++) {
        if (xQueueReceive(capture_queue, &frame, pdMS_TO_TICKS(1500)) != pdTRUE || frame.result != ESP_OK) break;
        snprintf(path, sizeof(path), "/grokbot/audio/%s/%d", sid, seq);
        response = exchange(path, frame.pcm, sizeof(frame.pcm), true);
        if (!response) break;
        bool listening = strcmp(field(response, "state"), "Listening") == 0;
        cJSON_Delete(response); response = NULL;
        if (!listening) break;
    }
    atomic_store(&capture_stop, true);
    /* Codec reads have a bounded driver timeout; join before closing I2S. */
    if (!(xEventGroupWaitBits(capture_events, BIT0, pdFALSE, pdTRUE, pdMS_TO_TICKS(2500)) & BIT0)) {
        show("Error", "", "Microphone did not stop. Restart the display.");
        configured = false;
        return;
    }
    esp_codec_dev_close(microphone);
    int played_version = 0;
    int64_t deadline = esp_timer_get_time() + 230000000LL;
    while (esp_timer_get_time() < deadline) {
        snprintf(path, sizeof(path), "/grokbot/session/%s", sid);
        response = exchange(path, NULL, 0, false);
        if (!response) goto fail;
        const char *state = field(response, "state");
        show(state, field(response, "transcript"), field(response, "spoken_text"));
        bool failed = strcmp(state, "Error") == 0;
        cJSON *v = cJSON_GetObjectItemCaseSensitive(response, "audio_version");
        int version = cJSON_IsNumber(v) ? v->valueint : 0;
        cJSON_Delete(response); response = NULL;
        if (failed) return;
        /* Always play the acknowledgement before a fast reply. */
        while (played_version < version) {
            if (!playback(sid, ++played_version)) goto fail;
        }
        if (played_version == 2) {
            snprintf(path, sizeof(path), "/grokbot/played/%s", sid);
            response = exchange(path, "{}", 2, false);
            if (!response) goto fail;
            show("Done", field(response, "transcript"), field(response, "spoken_text"));
            cJSON_Delete(response);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
fail:
    cJSON_Delete(response);
    if (microphone) esp_codec_dev_close(microphone);
    show("Error", "", "Voice unavailable. Please try again.");
}

static void worker(void *arg) {
    (void)arg;
    uint8_t event;
    for (;;) {
        if (xQueueReceive(requests, &event, portMAX_DELAY) != pdTRUE) continue;
        if (!configured) {
            show("Setup needed", "", "Configure the local voice bridge first.");
        } else if (!(xEventGroupGetBits(wifi_events) & CONNECTED)) {
            show("Offline", "", "Connect the display to Wi-Fi.");
        } else if (!codecs()) {
            show("Error", "", "Audio hardware unavailable.");
        } else {
            session();
        }
        xQueueReset(requests); /* Discard repeated taps received while busy. */
    }
}

void voice_device_request(void) {
    uint8_t event = 1;
    if (requests) xQueueSend(requests, &event, 0);
}

void voice_device_init(void) {
    requests = xQueueCreate(1, sizeof(uint8_t));
    wifi_events = xEventGroupCreate();
    capture_queue = xQueueCreate(8, sizeof(audio_frame_t));
    capture_events = xEventGroupCreate();
    if (!requests || !wifi_events || !capture_queue || !capture_events) { show("Error", "", "Not enough memory."); return; }
    /* Never erase an existing NVS partition to recover an init error. */
    if (nvs_flash_init() == ESP_OK) {
        nvs_handle_t nvs;
        if (nvs_open("grok_voice", NVS_READONLY, &nvs) == ESP_OK) {
            char ssid[33], password[65], token[160];
            configured = nvs_string(nvs, "ssid", ssid, sizeof(ssid)) &&
                nvs_string(nvs, "password", password, sizeof(password)) &&
                nvs_string(nvs, "url", base_url, sizeof(base_url)) &&
                nvs_string(nvs, "token", token, sizeof(token)) &&
                nvs_string(nvs, "device", device, sizeof(device));
            nvs_close(nvs);
            if (configured && (strncmp(base_url, "http://", 7) == 0 || strncmp(base_url, "https://", 8) == 0)) {
                snprintf(bearer, sizeof(bearer), "Bearer %s", token);
                memset(token, 0, sizeof(token));
                wifi_config_t cfg = {0};
                memcpy(cfg.sta.ssid, ssid, strlen(ssid));
                memcpy(cfg.sta.password, password, strlen(password));
                cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
                wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
                configured = esp_netif_init() == ESP_OK && esp_event_loop_create_default() == ESP_OK;
                if (configured) {
                    esp_netif_create_default_wifi_sta();
                    configured = esp_wifi_init(&init) == ESP_OK &&
                        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL) == ESP_OK &&
                        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL) == ESP_OK &&
                        esp_wifi_set_mode(WIFI_MODE_STA) == ESP_OK &&
                        esp_wifi_set_config(WIFI_IF_STA, &cfg) == ESP_OK && esp_wifi_start() == ESP_OK;
                }
                memset(&cfg, 0, sizeof(cfg));
                memset(password, 0, sizeof(password));
            } else configured = false;
        }
    }
    if (xTaskCreate(worker, "grok_voice", 16384, NULL, 4, NULL) != pdPASS)
        show("Error", "", "Voice worker unavailable.");
}
