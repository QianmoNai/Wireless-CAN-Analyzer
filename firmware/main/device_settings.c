#include "device_settings.h"
#include "network_link.h"
#include "uart_bridge.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "nvs.h"
#include <stdatomic.h>

static SemaphoreHandle_t guard;
static uint32_t current,persisted,retry_at;
static bool dirty,save_failed;
static atomic_bool initialized;
static uint32_t millis(void){return esp_timer_get_time()/1000;}
static bool save(uint32_t value) {
    nvs_handle_t h;
    if(nvs_open("local_panel",NVS_READWRITE,&h)!=ESP_OK)return false;
    esp_err_t e=nvs_set_u32(h,"settings",value);
    if(e==ESP_OK)e=nvs_commit(h);
    nvs_close(h);return e==ESP_OK;
}
uint32_t device_settings_read(ui_model_t *ui) {
    xSemaphoreTake(guard,portMAX_DELAY);
    uint32_t value=current;
    xSemaphoreGive(guard);
    ui_model_t saved;ui_model_init(&saved,value);
    ui->brightness=saved.brightness;ui->rotation=saved.rotation;
    ui->sound=saved.sound;ui->volume=saved.volume;
    ui->wifi=saved.wifi;ui->usb=saved.usb;ui->dual_role=saved.dual_role;
    ui->uart=saved.uart;ui->uart_baud=saved.uart_baud;
    // A remote mode change may remove the currently selected settings row.
    if(ui->selected>=ui_setting_count(ui)){ui->selected=0;ui->editing=false;}
    if(!ui->dual_role)ui->link_menu=false;
    return value;
}
void device_settings_update(const ui_model_t *ui,uint32_t expected) {
    uint32_t next=ui_model_settings(ui);
    xSemaphoreTake(guard,portMAX_DELAY);
    if(current==expected && current!=next){current=next;dirty=true;retry_at=millis()+1500;}
    xSemaphoreGive(guard);
}
bool device_settings_save_failed(void) {
    xSemaphoreTake(guard,portMAX_DELAY);bool failed=save_failed;xSemaphoreGive(guard);return failed;
}
int device_settings_command(const uint8_t p[20],bridge_packet_t *reply) {
    if(!settings_command_valid(p))return 0x13; // BLE_ATT_ERR_VALUE_NOT_ALLOWED
    if(!atomic_load(&initialized))return 0x11; // BLE_ATT_ERR_INSUFFICIENT_RES
    xSemaphoreTake(guard,portMAX_DELAY);
    ui_model_t ui;ui_model_init(&ui,current);
    unsigned result=0;
    if(p[2]) {
        settings_apply(&ui,p);uint32_t next=ui_model_settings(&ui);
        // Save before changing the shared or live configuration. Idempotent
        // requests avoid wear only when this exact value is already durable.
        if(next==persisted || save(next)) {
            current=persisted=next;dirty=false;save_failed=false;result=1;
        } else {save_failed=true;result=2;ui_model_init(&ui,current);}
    }
    *reply=(bridge_packet_t){.bytes={1,SETTINGS_EVENT,result,ui_mode(&ui),ui.uart}};
    wire_put_u32(reply->bytes+8,ui_uart_baud(&ui));
    wire_put_u32(reply->bytes+16,wire_u32(p+16));
    xSemaphoreGive(guard);return 0;
}
static void step(void) {
    xSemaphoreTake(guard,portMAX_DELAY);
    uint32_t value=current;
    if(dirty && (int32_t)(millis()-retry_at)>=0) {
        save_failed=!save(value);
        if(!save_failed){persisted=value;dirty=false;}else retry_at=millis()+10000;
    }
    xSemaphoreGive(guard);
    ui_model_t ui;ui_model_init(&ui,value);
    // Never hold the settings mutex while resetting a transport. In particular,
    // the USB parser holds its own mutex while executing a settings request.
    network_set_mode(ui_mode(&ui));uart_bridge_configure(ui.uart,ui_uart_baud(&ui));
}
static void worker(void *arg) {
    (void)arg;for(;;){step();vTaskDelay(pdMS_TO_TICKS(10));}
}
void device_settings_init(void) {
    guard=xSemaphoreCreateMutex();ESP_ERROR_CHECK(guard?ESP_OK:ESP_ERR_NO_MEM);
    nvs_handle_t h;uint32_t stored=0;
    if(nvs_open("local_panel",NVS_READONLY,&h)==ESP_OK) {
        nvs_get_u32(h,"settings",&stored);nvs_close(h);
    }
    ui_model_t ui;ui_model_init(&ui,stored);current=ui_model_settings(&ui);persisted=stored;
    atomic_store(&initialized,true);
    step();
    ESP_ERROR_CHECK(xTaskCreate(worker,"settings",3072,NULL,2,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
}
