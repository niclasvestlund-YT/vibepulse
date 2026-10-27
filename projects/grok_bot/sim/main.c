#include "lvgl.h"
#include "grok_ui.h"
#include <stdio.h>
#include <stdint.h>

static uint16_t frame[1024*600];
static uint16_t buffer[1024*40];
static void flush(lv_display_t *d, const lv_area_t *a, uint8_t *pixels) {
    uint16_t *src = (uint16_t *)pixels;
    for (int y=a->y1; y<=a->y2; ++y)
        for (int x=a->x1; x<=a->x2; ++x)
            frame[y*1024+x] = *src++;
    lv_display_flush_ready(d);
}
int main(int argc, char **argv) {
    /* Optional explicit fixtures: output.ppm state transcript reply.
     * These affect only the offline capture, never the device or host. */
    if (argc != 2 && argc != 5) return 2;
    lv_init();
    lv_display_t *d = lv_display_create(1024, 600);
    lv_display_set_buffers(d, buffer, NULL, sizeof(buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(d, flush);
    grok_ui_create(lv_screen_active());
    if (argc == 5) grok_ui_voice_update(argv[2], argv[3], argv[4]);
    lv_refr_now(d);
    FILE *f = fopen(argv[1], "wb");
    if (!f) return 3;
    fprintf(f, "P6\n1024 600\n255\n");
    for (int i=0; i<1024*600; ++i) {
        uint16_t v=frame[i];
        unsigned char rgb[] = {((v>>11)&31)*255/31, ((v>>5)&63)*255/63, (v&31)*255/31};
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
    return 0;
}
