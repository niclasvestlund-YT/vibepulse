#include "labs_features.h"
#ifdef ESP_PLATFORM
#include "nvs.h"

/* An independent namespace: never erase Wi-Fi, device keys or OTA state. */
tk_labs_store_result tk_labs_store_read(uint32_t *record) {
  nvs_handle_t handle;
  esp_err_t err = nvs_open("vp_labs", NVS_READONLY, &handle);
  if (err == ESP_ERR_NVS_NOT_FOUND) return TK_LABS_STORE_EMPTY;
  if (err != ESP_OK) return TK_LABS_STORE_ERROR;
  err = nvs_get_u32(handle, "features", record);
  nvs_close(handle);
  return err == ESP_OK ? TK_LABS_STORE_FOUND :
         err == ESP_ERR_NVS_NOT_FOUND ? TK_LABS_STORE_EMPTY : TK_LABS_STORE_ERROR;
}
bool tk_labs_store_write(uint32_t record) {
  nvs_handle_t handle;
  if (nvs_open("vp_labs", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t err = nvs_set_u32(handle, "features", record);
  if (err == ESP_OK) err = nvs_commit(handle);
  nvs_close(handle);
  return err == ESP_OK;
}
#else
/* No real user settings are touched by preview runs. */
static uint32_t saved;
static bool present;
tk_labs_store_result tk_labs_store_read(uint32_t *record) {
  *record = saved;
  return present ? TK_LABS_STORE_FOUND : TK_LABS_STORE_EMPTY;
}
bool tk_labs_store_write(uint32_t record) {
  saved = record;
  present = true;
  return true;
}
#endif
