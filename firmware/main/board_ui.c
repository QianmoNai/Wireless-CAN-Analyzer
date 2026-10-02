#include "sdkconfig.h"
#include "board_ui.h"
#if CONFIG_CAN_LOCAL_PANEL
#include <string.h>
#include <stdatomic.h>
#include "board_ui_render.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "nvs.h"

enum { PIN_BT1=1, PIN_BT2=2, PIN_BT3=7, PIN_BACKLIGHT=6, PIN_RESET=8,
       PIN_DC=9, PIN_CS=10, PIN_MOSI=11, PIN_CLOCK=12, PIN_BUZZER=13 };
static const char *TAG="board_ui";
static portMUX_TYPE snapshot_lock=portMUX_INITIALIZER_UNLOCKED;
static ui_snapshot_t shared={.bitrate=1000000};
static atomic_bool capture_frames;
static TaskHandle_t display_task;

void board_ui_observe(const bridge_packet_t *p)
{
    unsigned type=p->bytes[1];
    bool frame=type>=1 && type<=3;
    if (frame && (!atomic_load_explicit(&capture_frames,memory_order_relaxed) ||
        (type==3 && (p->bytes[2]&0xfc)==0x80))) return;
    if (!frame && type!=4 && type!=5 && type!=13 && type!=14) return;
    portENTER_CRITICAL(&snapshot_lock);
    ui_snapshot_packet(&shared,p->bytes);
    portEXIT_CRITICAL(&snapshot_lock);
}

static uint32_t load_settings(void)
{
    nvs_handle_t handle; uint32_t value=0;
    if (nvs_open("local_panel",NVS_READONLY,&handle)==ESP_OK) {
        nvs_get_u32(handle,"settings",&value); nvs_close(handle);
    }
    return value;
}
static esp_err_t save_settings(uint32_t value)
{
    nvs_handle_t handle;
    esp_err_t err=nvs_open("local_panel",NVS_READWRITE,&handle);
    if (err!=ESP_OK) return err;
    err=nvs_set_u32(handle,"settings",value);
    if (err==ESP_OK) err=nvs_commit(handle);
    nvs_close(handle); return err;
}
static bool color_done(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *event, void *ctx)
{
    BaseType_t wake=pdFALSE;
    vTaskNotifyGiveFromISR(display_task,&wake);
    return wake==pdTRUE;
}
static esp_err_t backlight(unsigned percent)
{
    esp_err_t err=ledc_set_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_0,percent*1023/100);
    return err==ESP_OK?ledc_update_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_0):err;
}
static esp_err_t setup_io(void)
{
    // Establish safe low outputs before installing PWM or panel drivers.
    gpio_set_level(PIN_BUZZER,0); gpio_set_level(PIN_BACKLIGHT,0);
    gpio_config_t outputs={.pin_bit_mask=(1ULL<<PIN_BUZZER)|(1ULL<<PIN_BACKLIGHT),.mode=GPIO_MODE_OUTPUT};
    esp_err_t err=gpio_config(&outputs); if (err!=ESP_OK) return err;
    gpio_config_t buttons={.pin_bit_mask=(1ULL<<PIN_BT1)|(1ULL<<PIN_BT2)|(1ULL<<PIN_BT3),
        .mode=GPIO_MODE_INPUT,.pull_up_en=GPIO_PULLUP_ENABLE};
    err=gpio_config(&buttons); if (err!=ESP_OK) return err;
    ledc_timer_config_t timer={.speed_mode=LEDC_LOW_SPEED_MODE,.duty_resolution=LEDC_TIMER_10_BIT,
        .timer_num=LEDC_TIMER_0,.freq_hz=20000,.clk_cfg=LEDC_AUTO_CLK};
    err=ledc_timer_config(&timer); if (err!=ESP_OK) return err;
    ledc_channel_config_t channel={.gpio_num=PIN_BACKLIGHT,.speed_mode=LEDC_LOW_SPEED_MODE,
        .channel=LEDC_CHANNEL_0,.timer_sel=LEDC_TIMER_0,.duty=0};
    return ledc_channel_config(&channel);
}
static esp_err_t setup_panel(esp_lcd_panel_handle_t *panel, esp_lcd_panel_io_handle_t *io)
{
    spi_bus_config_t bus={.sclk_io_num=PIN_CLOCK,.mosi_io_num=PIN_MOSI,.miso_io_num=-1,
        .quadwp_io_num=-1,.quadhd_io_num=-1,.max_transfer_sz=UI_WIDTH*UI_BAND_HEIGHT*2};
    esp_err_t err=spi_bus_initialize(SPI2_HOST,&bus,SPI_DMA_CH_AUTO);
    if (err!=ESP_OK) return err;
    esp_lcd_panel_io_spi_config_t config={.cs_gpio_num=PIN_CS,.dc_gpio_num=PIN_DC,
        .spi_mode=0,.pclk_hz=20000000,.trans_queue_depth=1,.lcd_cmd_bits=8,.lcd_param_bits=8,
        .on_color_trans_done=color_done};
    err=esp_lcd_new_panel_io_spi(SPI2_HOST,&config,io); if (err!=ESP_OK) goto fail;
    esp_lcd_panel_dev_config_t device={.reset_gpio_num=PIN_RESET,
#if CONFIG_CAN_LCD_BGR
        .rgb_ele_order=LCD_RGB_ELEMENT_ORDER_BGR,
#else
        .rgb_ele_order=LCD_RGB_ELEMENT_ORDER_RGB,
#endif
        .data_endian=LCD_RGB_DATA_ENDIAN_LITTLE,.bits_per_pixel=16};
    err=esp_lcd_new_panel_st7789(*io,&device,panel); if (err!=ESP_OK) goto fail;
    err=esp_lcd_panel_reset(*panel); if (err!=ESP_OK) goto fail;
    err=esp_lcd_panel_init(*panel); if (err!=ESP_OK) goto fail;
    err=esp_lcd_panel_set_gap(*panel,CONFIG_CAN_LCD_X_GAP,CONFIG_CAN_LCD_Y_GAP); if (err!=ESP_OK) goto fail;
#if CONFIG_CAN_LCD_INVERT
    err=esp_lcd_panel_invert_color(*panel,true); if (err!=ESP_OK) goto fail;
#endif
    return ESP_OK;
fail:
    if (*panel) { esp_lcd_panel_del(*panel); *panel=NULL; }
    if (*io) { esp_lcd_panel_io_del(*io); *io=NULL; }
    spi_bus_free(SPI2_HOST);
    return err;
}
static void ui_task(void *arg)
{
    display_task=xTaskGetCurrentTaskHandle();
    ui_model_t ui; ui_model_init(&ui,load_settings());
    uint32_t persisted=ui_model_settings(&ui), changed_at=0, retry_at=0;
    bool save_failed=false, panel_ready=false;
    ui_button_t keys[3]={0}; const int pins[]={PIN_BT1,PIN_BT2,PIN_BT3};
    esp_lcd_panel_handle_t panel=NULL; esp_lcd_panel_io_handle_t io=NULL;
    uint16_t *pixels=NULL;
    esp_err_t err=setup_io();
    if (err==ESP_OK) pixels=heap_caps_malloc(UI_WIDTH*UI_BAND_HEIGHT*2,MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL);
    if (err==ESP_OK && !pixels) err=ESP_ERR_NO_MEM;
    if (err==ESP_OK) err=setup_panel(&panel,&io);
    if (err!=ESP_OK) {
        ESP_LOGE(TAG,"Local panel init failed: %s; CAN/BLE continue",esp_err_to_name(err));
        gpio_set_level(PIN_BUZZER,0); heap_caps_free(pixels); vTaskDelete(NULL); return;
    }
    ESP_LOGI(TAG,"172x320 ST7789, SPI 20MHz, keys 1/2/7, backlight 6, buzzer 13");
    ui_snapshot_t snapshot={0}; ui_model_t drawn=ui;
    uint32_t next_frame=0, last_failed=0, last_alarm=0, beep_until=0;
    bool alarm_started=false, last_bus_off=false, applied_rotation=!ui.rotated;
    uint8_t applied_brightness=0;
    int band=0;
    for (;;) {
        uint32_t now=(uint32_t)(esp_timer_get_time()/1000);
        uint32_t before=ui_model_settings(&ui);
        for (unsigned i=0;i<3;i++) {
            ui_key_event_t event=ui_button_sample(&keys[i],gpio_get_level(pins[i])==0,now);
            if (event==KEY_NONE) continue;
            ui_model_key(&ui,i,event);
            if (ui.sound && (event==KEY_SHORT || i==2)) beep_until=now+25;
        }
        uint32_t current=ui_model_settings(&ui);
        if (current!=before) { changed_at=now; retry_at=now+1500; }
        if (current!=persisted && (uint32_t)(now-changed_at)>=1500 && (int32_t)(now-retry_at)>=0) {
            err=save_settings(current); save_failed=err!=ESP_OK;
            if (!save_failed) persisted=current;
            else { ESP_LOGW(TAG,"Settings not saved: %s",esp_err_to_name(err)); retry_at=now+10000; }
        }
        atomic_store_explicit(&capture_frames,ui.page==UI_TRAFFIC&&!ui.paused,memory_order_relaxed);
        if (panel_ready && ui.brightness!=applied_brightness) {
            err=backlight(ui.brightness);
            if (err!=ESP_OK) break;
            applied_brightness=ui.brightness;
        }
        if (band==0 && (int32_t)(now-next_frame)>=0) {
            ui_snapshot_t latest;
            portENTER_CRITICAL(&snapshot_lock); latest=shared; portEXIT_CRITICAL(&snapshot_lock);
            // Keep the visible frame list exactly frozen while paused.
            if (ui.page==UI_TRAFFIC && ui.paused) {
                memcpy(latest.frames,snapshot.frames,sizeof(latest.frames)); latest.frame_count=snapshot.frame_count;
            }
            snapshot=latest; snapshot.connected=ble_bridge_connected(); drawn=ui;
            bool new_fault=snapshot.failed!=last_failed || (snapshot.state==3&&!last_bus_off);
            last_failed=snapshot.failed; last_bus_off=snapshot.state==3;
            if (new_fault && (!alarm_started || (uint32_t)(now-last_alarm)>=3000)) {
                if (ui.sound) beep_until=now+100;
                last_alarm=now; alarm_started=true;
            }
            if (applied_rotation!=ui.rotated) {
                err=esp_lcd_panel_mirror(panel,ui.rotated,ui.rotated);
                if (err!=ESP_OK) break;
                applied_rotation=ui.rotated;
            }
            next_frame=now+250;
        } else if (band==0) goto idle;
        ui_render_band(pixels,band,&drawn,&snapshot,save_failed);
        err=esp_lcd_panel_draw_bitmap(panel,0,band,UI_WIDTH,band+UI_BAND_HEIGHT,pixels);
        if (err!=ESP_OK) break;
        // Buffer must remain untouched until DMA is complete. Only this task waits.
        if (!ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(100))) {
            err=ESP_ERR_TIMEOUT; break;
        }
        band+=UI_BAND_HEIGHT;
        if (band==UI_HEIGHT) {
            band=0;
            if (!panel_ready) {
                err=esp_lcd_panel_disp_on_off(panel,true);
                if (err!=ESP_OK) break;
                panel_ready=true;
            }
        }
idle:
        gpio_set_level(PIN_BUZZER,ui.sound && (int32_t)(beep_until-now)>0);
        vTaskDelay(pdMS_TO_TICKS(10)>0?pdMS_TO_TICKS(10):1);
    }
    // Retain DMA buffer/handles on transfer timeout: DMA may still own memory.
    atomic_store(&capture_frames,false); backlight(0); gpio_set_level(PIN_BUZZER,0);
    ESP_LOGE(TAG,"Local panel stopped: %s; CAN/BLE continue",esp_err_to_name(err));
    vTaskSuspend(NULL);
}
void board_ui_start(void)
{
    if (xTaskCreate(ui_task,"local_panel",4096,NULL,1,NULL)!=pdPASS)
        ESP_LOGE(TAG,"Cannot create local panel task; CAN/BLE continue");
}
#else
void board_ui_start(void) {}
void board_ui_observe(const bridge_packet_t *packet) { (void)packet; }
#endif
