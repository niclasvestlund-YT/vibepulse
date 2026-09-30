/* Round-first, static LVGL view shared by SDL and ESP32. No canvas buffers. */
#include "app_openpulse.h"
#include "torget.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
extern const lv_font_t plex_ui_14, plex_ui_21, plex_mono_24, plex_mono_40, plex_money_118, plex_icon_64;
static lv_obj_t *brand,*source,*title,*month,*budget,*day_title,*week_title,*day,*week;
static lv_obj_t *limit,*account,*byok,*status,*updated,*ring,*pager;
static op_snapshot data;
static bool has_data,failed;
static int selected,page;
static uint32_t received;
static uint32_t accent(void) {return has_data && data.level==2 ? 0xff697a : has_data && data.level==1 ? 0xeeb861 : 0x63d6c5;}
static void money(char *out,size_t cap,double value) {
    if(!isfinite(value)) snprintf(out,cap,"--");
    else if(fabs(value)>=1e6) snprintf(out,cap,"$%.1fM",value/1e6);
    else snprintf(out,cap,"$%.2f",value);
}
static lv_obj_t *label(lv_obj_t *root,int y,int width,const lv_font_t *font,uint32_t color,const char *text) {
    lv_obj_t *obj=lv_label_create(root);lv_obj_set_pos(obj,(480-width)/2,y);lv_obj_set_width(obj,width);
    lv_obj_set_style_text_font(obj,font,0);lv_obj_set_style_text_color(obj,lv_color_hex(color),0);
    lv_obj_set_style_text_align(obj,LV_TEXT_ALIGN_CENTER,0);lv_label_set_long_mode(obj,LV_LABEL_LONG_CLIP);
    lv_label_set_text(obj,text);return obj;
}
static void visible(lv_obj_t *obj,bool show) {if(show)lv_obj_remove_flag(obj,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(obj,LV_OBJ_FLAG_HIDDEN);}
static void render(void) {
    if(!month) return;
    char a[40],b[40],line[120];
    double elapsed=has_data ? (lv_tick_get()-received)/1000.0 : 0;
    const char *name=has_data ? data.name : "API key";
    snprintf(line,sizeof line,"%s%s",has_data && data.demo ? "DEMO / " : "",name);
    lv_label_set_text(source,line);
    lv_point_t size;lv_text_get_size(&size,line,&plex_ui_14,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
    if(size.x>290) {snprintf(line,sizeof line,"%s%.16s...",has_data && data.demo ? "DEMO / " : "",name);lv_label_set_text(source,line);}
    bool details=page==1;
    lv_label_set_text(title,details ? "KEY LIMIT LEFT / USD" : "MONTH SPEND / USD / UTC");
    lv_obj_set_y(title,126);
    lv_obj_set_y(month,details ? 155 : 149);
    double hero=has_data ? (details ? data.remaining : data.month) : NAN;
    if(details && has_data && data.unlimited) snprintf(a,sizeof a,"Unlimited");
    else if(!isfinite(hero)) snprintf(a,sizeof a,"\xe2\x80\x93");
    else if(fabs(hero)<1e6) snprintf(a,sizeof a,"%.2f",hero);
    else snprintf(a,sizeof a,"%.1fM",hero/1e6);
    const lv_font_t *font=&plex_mono_40;
    if(!details && (!isfinite(hero) || (hero>=0 && hero<1000))) font=&plex_money_118;
    lv_text_get_size(&size,a,font,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
    if(size.x>366) font=&plex_mono_40;
    if(font==&plex_mono_40 && !isfinite(hero) && !(details && has_data && data.unlimited)) snprintf(a,sizeof a,"--");
    lv_obj_set_style_text_font(month,font,0);lv_label_set_text(month,a);
    money(a,sizeof a,has_data ? data.budget : NAN);
    if(details) snprintf(line,sizeof line,"Reset %s / BYOK in limit: %s",has_data && data.reset[0] ? data.reset : "--", !has_data || data.byok_in_limit<0 ? "?" : data.byok_in_limit ? "yes" : "no");
    else if(has_data && isfinite(data.percent) && data.percent>999) snprintf(line,sizeof line,">999%% of %s budget",a);
    else if(has_data && isfinite(data.percent)) snprintf(line,sizeof line,"%.0f%% of %s budget",data.percent,a);
    else snprintf(line,sizeof line,"Display budget %s",a);
    lv_label_set_text(budget,line);lv_obj_set_y(budget,details ? 205 : 275);
    lv_obj_set_style_text_font(budget,details ? &plex_ui_14 : &plex_ui_21,0);
    lv_obj_set_style_text_color(budget,lv_color_hex(details ? 0xa9b7c9 : accent()),0);
    lv_arc_set_value(ring,has_data && isfinite(data.percent) ? (int)fmin(100,fmax(0,data.percent)) : 0);
    lv_obj_set_style_arc_color(ring,lv_color_hex(accent()),LV_PART_INDICATOR);
    visible(ring,!details);visible(day_title,!details);visible(week_title,!details);visible(day,!details);visible(week,!details);
    money(a,sizeof a,has_data ? data.day : NAN);lv_label_set_text(day,a);
    money(a,sizeof a,has_data ? data.week : NAN);lv_label_set_text(week,a);
    visible(limit,details);visible(account,details);visible(byok,details);
    lv_label_set_text(limit,has_data && data.account_enabled ? "ACCOUNT CREDITS / USD" : "ACCOUNT VIEW OFF");
    money(a,sizeof a,has_data && data.account_enabled ? data.balance : NAN);lv_label_set_text(account,a);
    bool account_old=has_data && isfinite(data.account_age) && data.account_age+elapsed>=180;
    const char *as=failed && data.account_enabled ? "connection error" : account_old && !strcmp(data.account_state,"fresh") ? "stale" : data.account_state;
    money(a,sizeof a,has_data ? data.byok : NAN);
    snprintf(line,sizeof line,"BYOK month %s / separate",a);lv_label_set_text(byok,line);
    bool old=has_data && isfinite(data.age) && data.age+elapsed>=180;
    const char *state=failed ? "CONNECTION ERROR" : !has_data ? "NO DATA" :
        !strcmp(data.state,"error") ? "SOURCE ERROR" : old || !strcmp(data.state,"stale") ? "STALE" :
        !strcmp(data.state,"no_data") ? "NO DATA" : "UPDATED";
    if(has_data && isfinite(data.age)) snprintf(b,sizeof b," / %.0fs ago",data.age+elapsed);else b[0]=0;
    snprintf(line,sizeof line,"%s%s",state,b);lv_label_set_text(status,line);
    lv_obj_set_y(status,details ? 367 : 381);
    lv_obj_set_style_text_color(status,lv_color_hex(failed || old || strcmp(data.state,"fresh") ? 0xeeb861 : 0xa9b7c9),0);
    if(details && has_data && data.account_enabled) snprintf(line,sizeof line,"Account: %s",as);
    else snprintf(line,sizeof line,"%s",has_data && data.updated[0] ? data.updated : "Last update: --");
    lv_label_set_text(updated,line);lv_obj_set_y(updated,details ? 392 : 404);
    lv_label_set_text(pager,details ? "1  /  [2] DETAILS" : "[1] SPEND  /  2");
}
static void tick(lv_timer_t *timer) {(void)timer;render();}
void openpulse_set_page(int value) {page=value==1 ? 1 : 0;render();}
static void change_page(lv_event_t *event) {(void)event;openpulse_set_page(1-page);}
static void next_key(lv_event_t *event) {
    (void)event;
    if(has_data && data.key_count>1) {selected=(selected+1)%data.key_count;has_data=false;failed=false;render();}
}
static void create(lv_obj_t *root) {
    lv_obj_set_style_bg_color(root,lv_color_black(),0);
    ring=lv_arc_create(root);lv_obj_set_size(ring,430,430);lv_obj_set_pos(ring,25,25);
    lv_arc_set_rotation(ring,135);lv_arc_set_bg_angles(ring,0,270);lv_arc_set_range(ring,0,100);
    lv_obj_set_style_arc_width(ring,5,LV_PART_MAIN);lv_obj_set_style_arc_width(ring,6,LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(ring,lv_color_hex(0x1d2b30),LV_PART_MAIN);
    lv_obj_remove_style(ring,NULL,LV_PART_KNOB);lv_obj_remove_flag(ring,LV_OBJ_FLAG_CLICKABLE);
    brand=label(root,65,220,&plex_ui_21,0xffffff,"OPENPULSE");
    source=label(root,96,290,&plex_ui_14,0x63d6c5,"API key");
    lv_obj_add_flag(source,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(source,next_key,LV_EVENT_CLICKED,NULL);
    title=label(root,126,320,&plex_ui_14,0xa9b7c9,"MONTH SPEND / USD / UTC");
    month=label(root,149,366,&plex_money_118,0xffffff,"\xe2\x80\x93");
    budget=label(root,275,320,&plex_ui_21,0x63d6c5,"Display budget --");
    day_title=label(root,314,150,&plex_ui_14,0xa9b7c9,"TODAY");lv_obj_set_x(day_title,85);
    week_title=label(root,314,150,&plex_ui_14,0xa9b7c9,"THIS WEEK");lv_obj_set_x(week_title,245);
    day=label(root,337,170,&plex_mono_24,0xffffff,"--");lv_obj_set_x(day,75);
    week=label(root,337,170,&plex_mono_24,0xffffff,"--");lv_obj_set_x(week,235);
    limit=label(root,254,330,&plex_ui_14,0xa9b7c9,"ACCOUNT VIEW OFF");
    account=label(root,277,340,&plex_mono_40,0xffffff,"--");
    byok=label(root,333,340,&plex_ui_14,0xa9b7c9,"BYOK month -- / separate");
    status=label(root,381,300,&plex_ui_14,0xa9b7c9,"NO DATA");
    updated=label(root,404,270,&plex_ui_14,0xa9b7c9,"Last update: --");
    pager=label(root,433,180,&plex_ui_14,0x63d6c5,"[1] SPEND / 2");
    lv_obj_add_flag(pager,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(pager,change_page,LV_EVENT_CLICKED,NULL);
    lv_obj_add_flag(root,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(root,change_page,LV_EVENT_CLICKED,NULL);
    lv_timer_create(tick,1000,NULL);render();openpulse_net_start();
}
void openpulse_apply(const op_snapshot *value) {if(value->key_index!=selected)return;data=*value;has_data=true;failed=false;received=lv_tick_get();render();torget_data_alive();}
void openpulse_failed(void) {failed=true;render();}
int openpulse_key_index(void) {return selected;}
const torget_app_t openpulse_app={.api_version=TORGET_APP_API_VERSION,.name="OPENPULSE",
    .icon={.font=&plex_icon_64,.glyph="T",.plate_hex=0x152b2a,.glyph_hex=0x63d6c5,.dot_hex=0x63d6c5},.create=create};
bool openpulse_layout_valid(void) {
    lv_obj_t *labels[]={brand,source,title,month,budget,day_title,week_title,day,week,limit,account,byok,status,updated,pager};
    for(size_t i=0;i<sizeof labels/sizeof labels[0];i++) {
        lv_obj_t *obj=labels[i];if(lv_obj_has_flag(obj,LV_OBJ_FLAG_HIDDEN))continue;
        lv_point_t size;lv_text_get_size(&size,lv_label_get_text(obj),lv_obj_get_style_text_font(obj,0),0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
        if(size.x>lv_obj_get_width(obj) || lv_obj_get_y(obj)+size.y>466) {fprintf(stderr,"OpenPulse label %u: width %d / %d\n",(unsigned)i,(int)size.x,(int)lv_obj_get_width(obj));return false;}
    }
    return true;
}
