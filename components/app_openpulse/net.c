#include "app_openpulse.h"
#include "torget.h"
#include "torget_http.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>
#include "esp_log.h"
#include "openpulse_panel_config.h"
/* Explicit OpenPulse origin only; never VibePulse discovery or relays. */
static void poll(void *arg) {
    (void)arg;static char body[4096];char url[256];size_t length;
    bool received=false;
    torget_net_wait();
    for (;;) {
        torget_ui_lock();int index=openpulse_key_index();torget_ui_unlock();
        snprintf(url,sizeof url,"%s/api/openpulse?key=%d",OPENPULSE_SERVICE_ORIGIN,index);
        op_snapshot snapshot;
        bool ok=torget_http_get(url,body,sizeof body,&length) && op_parse(body,length,&snapshot);
        torget_ui_lock();
        if(ok) openpulse_apply(&snapshot);else openpulse_failed();
        torget_ui_unlock();
        if(ok && !received) ESP_LOGI("openpulse", "Summary received: key=%d state=%s demo=%d",
                                     snapshot.key_index, snapshot.state, snapshot.demo);
        received=ok;
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
void openpulse_net_start(void) {
    if(xTaskCreate(poll,"openpulse",5120,NULL,4,NULL)!=pdPASS) openpulse_failed();
}
