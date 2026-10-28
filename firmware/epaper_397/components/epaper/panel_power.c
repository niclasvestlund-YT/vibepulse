#include "panel_power.h"
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// Factory-board path: TG28/AXP2101 compatible PMU, ALDO3 = EPD_VCC.
// Same values as Waveshare axp_prot + XPowers. No charger configuration.
static i2c_master_dev_handle_t pmu;
static esp_err_t read_reg(uint8_t reg, uint8_t *value) {
    return i2c_master_transmit_receive(pmu,&reg,1,value,1,500);
}
static esp_err_t write_reg(uint8_t reg,uint8_t value) {
    uint8_t bytes[]={reg,value};
    return i2c_master_transmit(pmu,bytes,sizeof(bytes),500);
}
esp_err_t panel_power_init(void) {
    i2c_master_bus_handle_t bus;
    i2c_master_bus_config_t cfg={.i2c_port=0,.sda_io_num=41,.scl_io_num=42,
        .clk_source=I2C_CLK_SRC_DEFAULT,.glitch_ignore_cnt=7,
        .flags.enable_internal_pullup=true};
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&cfg,&bus),"paper-pmu","bus");
    i2c_device_config_t dev={.dev_addr_length=I2C_ADDR_BIT_LEN_7,
        .device_address=0x34,.scl_speed_hz=400000};
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(bus,&dev,&pmu),"paper-pmu","device");
    uint8_t id,voltage;
    ESP_RETURN_ON_ERROR(read_reg(0x03,&id),"paper-pmu","chip id");
    if(id!=0x4a)return ESP_ERR_INVALID_RESPONSE;
    ESP_RETURN_ON_ERROR(read_reg(0x94,&voltage),"paper-pmu","ALDO3 voltage");
    voltage=(voltage&0xe0)|28; // (3300-500)/100, preserve upper bits.
    ESP_RETURN_ON_ERROR(write_reg(0x94,voltage),"paper-pmu","set ALDO3 voltage");
    uint8_t actual;
    ESP_RETURN_ON_ERROR(read_reg(0x94,&actual),"paper-pmu","verify voltage");
    return actual==voltage ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}
void panel_power_on(void) {
    uint8_t value;
    ESP_ERROR_CHECK(read_reg(0x90,&value));
    ESP_ERROR_CHECK(write_reg(0x90,value|4));
    uint8_t actual;
    ESP_ERROR_CHECK(read_reg(0x90,&actual));
    ESP_ERROR_CHECK((actual&4) ? ESP_OK : ESP_ERR_INVALID_RESPONSE);
    vTaskDelay(pdMS_TO_TICKS(30));
}
void panel_power_off(void) {
    uint8_t value;
    ESP_ERROR_CHECK(read_reg(0x90,&value));
    ESP_ERROR_CHECK(write_reg(0x90,value&~4));
}
