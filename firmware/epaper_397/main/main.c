#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "driver/gpio.h"
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_rom_crc.h"
#include "freertos/semphr.h"
#include "epaper_port.h"
#include "panel_power.h"

#define FRAME_BYTES 48000
#define VERSION "paper-0.2.0"
extern const uint8_t boot_raw_start[] asm("_binary_boot_raw_start");
extern const uint8_t offline_raw_start[] asm("_binary_offline_raw_start");
static SemaphoreHandle_t tx_lock;
static void reply(const char *s) {
    xSemaphoreTake(tx_lock, portMAX_DELAY);
    usb_serial_jtag_write_bytes(s, strlen(s), pdMS_TO_TICKS(250));
    xSemaphoreGive(tx_lock);
}
static bool read_exact(uint8_t *p, size_t n, int timeout_ms) {
    int64_t end = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    size_t got = 0;
    while (got < n && esp_timer_get_time() < end) {
        int r = usb_serial_jtag_read_bytes(p + got, n - got, pdMS_TO_TICKS(20));
        if (r > 0) got += r;
    }
    return got == n;
}
static void buttons(void *unused) {
    const int pins[] = {4, 5, 6};
    const char *events[] = {"BUTTON prev\n", "BUTTON home\n", "BUTTON next\n"};
    gpio_config_t cfg = {.pin_bit_mask = (1ULL<<4)|(1ULL<<5)|(1ULL<<6),
        .mode=GPIO_MODE_INPUT, .pull_up_en=GPIO_PULLUP_ENABLE};
    ESP_ERROR_CHECK(gpio_config(&cfg));
    int count[] = {0,0,0};
    bool held[] = {false,false,false};
    while (1) {
        for (int i=0;i<3;i++) {
            if (!gpio_get_level(pins[i])) {
                if (count[i] < 3) count[i]++;
                if (count[i] == 3 && !held[i]) {held[i]=true;reply(events[i]);}
            } else {count[i]=0;held[i]=false;}
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
static unsigned partials = 10;
static const char *draw(const uint8_t *frame) {
    // Keep the panel RAM/power between partial refreshes. Cycling ALDO3 or
    // deep-sleeping the controller would destroy the differential baseline.
    if (partials >= 10) {
        EPD_Init();
        EPD_Display_Base(frame);
        partials = 0;
        return "full";
    }
    EPD_Display_Partial(frame, 0, 0, EPD_WIDTH, EPD_HEIGHT);
    partials++;
    return "partial";
}
void app_main(void) {
    tx_lock = xSemaphoreCreateMutex();
    assert(tx_lock);
    usb_serial_jtag_driver_config_t cfg = {.rx_buffer_size=8192,.tx_buffer_size=2048};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    uint8_t *frame = heap_caps_malloc(FRAME_BYTES, MALLOC_CAP_8BIT);
    assert(frame);
    epaper_port_init();
    ESP_ERROR_CHECK(panel_power_init());
    reply("PMU ALDO3 3300mV configured and readback verified\n");
    draw(boot_raw_start);
    reply("HELLO VPP1 waveshare_epaper_397 " VERSION " 800 480\n");
    xTaskCreate(buttons, "paper-buttons", 3072, NULL, 3, NULL);
    int64_t last_frame = esp_timer_get_time();
    bool offline = false;
    unsigned match = 0;
    while (1) {
        uint8_t b;
        if (!read_exact(&b, 1, 500)) {
            if (!offline && esp_timer_get_time()-last_frame > 120000000) {
                draw(offline_raw_start); offline=true;
                reply("OFFLINE no-host-frame\n");
            }
            continue;
        }
        const char *magic = "VPP1";
        if (b == '!' && match == 0) {
            last_frame=esp_timer_get_time();
            reply(offline ? "PONG repaint-required\n" : "PONG pixels-unchanged\n");
            continue;
        }
        if (b == '?' && match == 0) {
            reply("HELLO VPP1 waveshare_epaper_397 " VERSION " 800 480\n");
            continue;
        }
        if (b == magic[match]) match++;
        else match = b == 'V' ? 1 : 0;
        if (match < 4) continue;
        match=0;
        uint32_t h[3];
        if (!read_exact((uint8_t *)h, sizeof(h), 1000)) {reply("ERROR header-timeout\n");continue;}
        if (h[1] != FRAME_BYTES) {reply("ERROR frame-length\n");continue;}
        if (!read_exact(frame, FRAME_BYTES, 15000)) {reply("ERROR frame-timeout\n");continue;}
        uint32_t crc=esp_rom_crc32_le(0, frame, FRAME_BYTES);
        if (crc != h[2]) {reply("ERROR frame-crc\n");continue;}
        int64_t started=esp_timer_get_time();
        const char *mode=draw(frame);
        last_frame=esp_timer_get_time();offline=false;
        char ack[128];
        snprintf(ack,sizeof(ack),"ACK %" PRIu32 " %08" PRIx32 " %" PRId64 "ms %s\n",
                 h[0],crc,(last_frame-started)/1000,mode);
        reply(ack);
    }
}
