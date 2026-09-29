/* Thin desktop adapter: same parser and LVGL app as firmware. */
#include <SDL.h>
#include <curl/curl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "app_openpulse.h"
#include "display_geometry.h"
#include "wifi_status_assets.h"
#include "lvgl.h"
#include "torget.h"
void openpulse_net_start(void) {}
void torget_data_alive(void) {}
static char payload[4096];
static size_t used;
static size_t receive(void *bytes,size_t size,size_t count,void *context) {
    (void)context;size_t length=size*count;
    if(length>=sizeof payload-used) return 0;
    memcpy(payload+used,bytes,length);used+=length;payload[used]=0;return length;
}
static bool fetch(const char *origin) {
    used=0;
    char url[512];snprintf(url,sizeof url,"%s/api/openpulse?key=%d",origin,openpulse_key_index());
    CURL *curl=curl_easy_init();if(!curl) return false;
    curl_easy_setopt(curl,CURLOPT_URL,url);curl_easy_setopt(curl,CURLOPT_TIMEOUT_MS,1500L);
    curl_easy_setopt(curl,CURLOPT_WRITEFUNCTION,receive);curl_easy_setopt(curl,CURLOPT_FAILONERROR,1L);
    CURLcode result=curl_easy_perform(curl);curl_easy_cleanup(curl);
    return result==CURLE_OK;
}
static int capture(const char *path) {
    lv_obj_update_layout(lv_screen_active());lv_refr_now(NULL);
    if(!openpulse_layout_valid()) {fprintf(stderr,"OpenPulse label overflow\n");return 2;}
    lv_draw_buf_t *buf=lv_snapshot_take(lv_screen_active(),LV_COLOR_FORMAT_XRGB8888);
    if(!buf) return 1;
    SDL_Surface *surface=SDL_CreateRGBSurfaceFrom(buf->data,buf->header.w,buf->header.h,32,buf->header.stride,
        0x00ff0000,0x0000ff00,0x000000ff,0);
    int result=surface ? SDL_SaveBMP(surface,path) : -1;
    if(surface) SDL_FreeSurface(surface);
    lv_draw_buf_destroy(buf);return result ? 1 : 0;
}
int main(int argc,char **argv) {
    int page=0;
    const char *origin="http://127.0.0.1:8738", *fixture=NULL, *output=NULL;
    for(int i=1;i<argc;i++) {
        if(i+1<argc && !strcmp(argv[i],"--page")) page=atoi(argv[++i]);
        else if(i+1<argc && !strcmp(argv[i],"--url")) origin=argv[++i];
        else if(i+1<argc && !strcmp(argv[i],"--fixture")) fixture=argv[++i];
        else if(i+1<argc && !strcmp(argv[i],"--capture")) output=argv[++i];
        else {fprintf(stderr,"Use --url OR --fixture, optionally --capture output.bmp\n");return 2;}
    }
    lv_init();lv_display_t *display=lv_sdl_window_create(TG_DISPLAY_WIDTH,TG_DISPLAY_HEIGHT);
    lv_sdl_window_set_title(display,"OpenPulse / click to change page, name to change key");lv_sdl_mouse_create();
    lv_obj_remove_flag(lv_screen_active(),LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(lv_screen_active(),LV_SCROLLBAR_MODE_OFF);
    lv_obj_t *root=lv_obj_create(lv_screen_active());lv_obj_remove_style_all(root);
    tg_position_viewport(root);lv_obj_set_style_bg_opa(root,255,0);lv_obj_remove_flag(root,LV_OBJ_FLAG_SCROLLABLE);
    openpulse_app.create(root);openpulse_set_page(page);
    lv_obj_t *wifi=lv_image_create(root);lv_image_set_src(wifi,&tg_img_wifi_strong);
#ifdef TORGET_BOARD_175
    lv_obj_set_pos(wifi,230,43);
#else
    lv_obj_set_pos(wifi,426,28);
#endif
    uint32_t last=0;bool first=true;
    for(;;) {
        uint32_t now=lv_tick_get();
        if(first || (!fixture && now-last>=3000)) {
            first=false;bool ok;
            if(fixture) {
                FILE *file=fopen(fixture,"rb");if(!file) return 2;
                used=fread(payload,1,sizeof payload-1,file);ok=!ferror(file)&&feof(file);fclose(file);payload[used]=0;
            } else ok=fetch(origin);
            op_snapshot snapshot;
            if(ok && op_parse(payload,used,&snapshot)) openpulse_apply(&snapshot);else openpulse_failed();
            last=lv_tick_get();
            if(output) {lv_timer_handler();return capture(output);}
        }
        lv_timer_handler();usleep(5000);
    }
}
