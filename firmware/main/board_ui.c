#include "sdkconfig.h"
#include "board_ui.h"
#include "uart_bridge.h"
#include "network_link.h"
#include "dual_link.h"
#if CONFIG_CAN_LOCAL_PANEL
#include "board_ui_render.h"
#include "board_ui_animation.h"
#include "board_buzzer.h"
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
#ifdef CAN_PANEL_BENCHMARK
#include <stdio.h>
#include <inttypes.h>
#endif

enum { PIN_BT1=1, PIN_BT2=2, PIN_BT3=7, PIN_BACKLIGHT=6, PIN_RESET=8,
       PIN_DC=9, PIN_CS=10, PIN_MOSI=11, PIN_CLOCK=12 };
static const char *TAG="board_ui";
static portMUX_TYPE snapshot_lock=portMUX_INITIALIZER_UNLOCKED;
static ui_snapshot_t shared={.bitrate=1000000};
static TaskHandle_t display_task;

void board_ui_observe(const bridge_packet_t *p)
{
    unsigned type=p->bytes[1];
    // The local panel only observes aggregate statistics, never frame payloads.
    if (type!=4 && type!=5 && type!=13 && type!=14) return;
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
    // IDF 6.0.2's SPI panel post-callback discards this function's bool.
    // Yield here so a completed DMA does not wait for the 100Hz OS tick.
    if(wake==pdTRUE)portYIELD_FROM_ISR();
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
    gpio_set_level(PIN_BACKLIGHT,0);
    gpio_config_t outputs={.pin_bit_mask=1ULL<<PIN_BACKLIGHT,.mode=GPIO_MODE_OUTPUT};
    esp_err_t err=gpio_config(&outputs); if (err!=ESP_OK) return err;
    err=board_buzzer_init(); if(err!=ESP_OK)return err;
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
        .quadwp_io_num=-1,.quadhd_io_num=-1,.max_transfer_sz=UI_DMA_PIXELS*2};
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
typedef struct {
    ui_model_t model;
    ui_snapshot_t snapshot;
    ui_render_pose_t pose;
    bool save_failed;
} ui_frame_t;

static void ui_task(void *arg)
{
    display_task=xTaskGetCurrentTaskHandle();
    ui_model_t ui; ui_model_init(&ui,load_settings());
    ui_animation_t animation;ui_animation_init(&animation,&ui);
    network_set_mode(ui_mode(&ui));
    uart_bridge_configure(ui.uart,ui_uart_baud(&ui));
    uint32_t persisted=ui_model_settings(&ui), changed_at=0, retry_at=0;
    bool save_failed=false, panel_ready=false;
    ui_button_t keys[3]={0}; const int pins[]={PIN_BT1,PIN_BT2,PIN_BT3};
    esp_lcd_panel_handle_t panel=NULL; esp_lcd_panel_io_handle_t io=NULL;
    uint16_t *pixels[2]={NULL,NULL};
    esp_err_t err=setup_io();
    for(unsigned i=0;i<2 && err==ESP_OK;i++){
        pixels[i]=heap_caps_malloc(UI_DMA_PIXELS*2,MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL);
        if(!pixels[i])err=ESP_ERR_NO_MEM;
    }
    if (err==ESP_OK) err=setup_panel(&panel,&io);
    if (err!=ESP_OK) {
        ESP_LOGE(TAG,"Local panel init failed: %s; CAN/BLE continue",esp_err_to_name(err));
        board_buzzer_stop();heap_caps_free(pixels[0]);heap_caps_free(pixels[1]);vTaskDelete(NULL);return;
    }
    ESP_LOGI(TAG,"172x320 ST7789, SPI 20MHz, animated UI, keys 1/2/7");
    ui_frame_t frame={0}, previous={0};
    uint32_t next_frame=0, last_failed=0, last_alarm=0, beep_until=0, bands=0;
    bool alarm_started=false, last_bus_off=false, beep_active=false;
    bool requested=true, rendering=false, transfer_pending=false, previous_valid=false;
    uint8_t applied_brightness=0, applied_rotation=UINT8_MAX;
    uint32_t uart_last_ms=0,uart_last_rx=0,uart_last_tx=0,uart_rx_hz=0,uart_tx_hz=0;
    unsigned buffer=0;
#ifdef CAN_PANEL_BENCHMARK
    uint32_t benchmark_next=2000;
    unsigned benchmark_action=0, benchmark_bands=0;
    int64_t benchmark_start=0;
#endif
    for (;;) {
        uint32_t now=(uint32_t)(esp_timer_get_time()/1000);
        uint32_t before=ui_model_settings(&ui);
#ifdef CAN_PANEL_BENCHMARK
        // Local, non-persistent navigation only: no network or CAN commands.
        if(!rendering && benchmark_action<4 && (int32_t)(now-benchmark_next)>=0){
            ui_model_key(&ui,1,(benchmark_action&1)?KEY_LONG:KEY_SHORT);
            requested=true;benchmark_action++;benchmark_next=now+1200;
            printf("PANEL_ACTION %u at=%"PRIu32" page=%u\n",benchmark_action,now,ui.page);
        }
#endif
        for (unsigned i=0;i<3;i++) {
            ui_key_event_t event=ui_button_sample(&keys[i],gpio_get_level(pins[i])==0,now);
            if (event==KEY_NONE) continue;
            uint8_t previous_volume=ui.volume;
            if(ui.link_menu) {
                ui_key_role_t key=ui_key_role(ui.rotation,i);
                if(event==KEY_LONG&&key==UI_KEY_ACTION){dual_link_button(DUAL_BUTTON_CANCEL);ui.link_menu=false;}
                else if(event==KEY_SHORT)dual_link_button(key==UI_KEY_ACTION?DUAL_BUTTON_MIDDLE:key==UI_KEY_PREVIOUS?DUAL_BUTTON_PREVIOUS:DUAL_BUTTON_NEXT);
            } else ui_model_key(&ui,i,event);
            requested=true;
            if (ui.sound && (event==KEY_SHORT || ui_key_role(ui.rotation,i)==UI_KEY_ACTION)) {
                beep_until=now+(previous_volume!=ui.volume?80:25);beep_active=true;
            }
        }
        ui_animation_target(&animation,&ui,now);
        ui_render_pose_t pose;
        bool animating=ui_animation_sample(&animation,now,&pose);
        network_set_mode(ui_mode(&ui));
        uart_bridge_configure(ui.uart,ui_uart_baud(&ui));
        if(!ui.sound || (beep_active && (int32_t)(beep_until-now)<=0))beep_active=false;
        err=board_buzzer_set(beep_active,ui.volume);
        if(err!=ESP_OK)break;
        uint32_t current=ui_model_settings(&ui);
        if (current!=before) { changed_at=now; retry_at=now+1500; }
        // Flash commits cannot pause an animation or extend a short beep.
        if (!rendering && !animating && !beep_active && current!=persisted &&
            (uint32_t)(now-changed_at)>=1500 && (int32_t)(now-retry_at)>=0) {
            err=save_settings(current);
            bool failed=err!=ESP_OK;
            if(failed!=save_failed)requested=true;
            save_failed=failed;
            if (!save_failed) persisted=current;
            else { ESP_LOGW(TAG,"Settings not saved: %s",esp_err_to_name(err)); retry_at=now+10000; }
        }
        if (panel_ready && ui.brightness!=applied_brightness) {
            err=backlight(ui.brightness);
            if (err!=ESP_OK) break;
            applied_brightness=ui.brightness;
        }
        if (!rendering && (requested || (int32_t)(now-next_frame)>=0)) {
            frame.model=ui;frame.pose=pose;frame.save_failed=save_failed;
            portENTER_CRITICAL(&snapshot_lock);frame.snapshot=shared;portEXIT_CRITICAL(&snapshot_lock);
            frame.snapshot.connected=ble_bridge_connected();
            uint32_t code;network_display(&frame.snapshot.wifi_state,frame.snapshot.ip,&code);
            frame.snapshot.pairing=code!=UINT32_MAX&&!ui.dual_role&&!ui.usb;frame.snapshot.pairing_code=code;
            dual_link_view(&frame.snapshot.dual);
            if(ui.dual_role){frame.snapshot.connected=dual_link_connected();if(frame.snapshot.wifi_state==4)frame.snapshot.dual.state=DUAL_UI_ERROR;}
            if(ui.uart){
                uart_bridge_stats_t serial;uart_bridge_stats(&serial);
                uint32_t elapsed=now-uart_last_ms;
                if(elapsed>=500){uart_rx_hz=(uint64_t)(serial.rx-uart_last_rx)*1000/elapsed;uart_tx_hz=(uint64_t)(serial.tx-uart_last_tx)*1000/elapsed;uart_last_ms=now;uart_last_rx=serial.rx;uart_last_tx=serial.tx;}
                frame.snapshot.bitrate=serial.baud;frame.snapshot.rx_hz=uart_rx_hz;frame.snapshot.tx_hz=uart_tx_hz;
                frame.snapshot.rates_valid=true;frame.snapshot.state=serial.ready?0:3;
                frame.snapshot.failed=serial.dropped;frame.snapshot.load_valid=false;
            }else{uart_last_ms=now;uart_bridge_stats_t serial;uart_bridge_stats(&serial);uart_last_rx=serial.rx;uart_last_tx=serial.tx;uart_rx_hz=uart_tx_hz=0;}
            bool new_fault=frame.snapshot.failed!=last_failed || (frame.snapshot.state==3&&!last_bus_off);
            last_failed=frame.snapshot.failed;last_bus_off=frame.snapshot.state==3;
            if (new_fault && (!alarm_started || (uint32_t)(now-last_alarm)>=3000)) {
                if (ui.sound) {beep_until=now+100;beep_active=true;}
                last_alarm=now; alarm_started=true;
            }
            if (applied_rotation!=ui.rotation) {
                // Only completed frames reach here; no DMA is using either buffer.
                if (panel_ready) {
                    err=esp_lcd_panel_disp_on_off(panel,false);
                    if (err!=ESP_OK) break;
                    panel_ready=false;
                }
                ui_panel_geometry_t geometry=ui_panel_geometry(ui.rotation,CONFIG_CAN_LCD_X_GAP,CONFIG_CAN_LCD_Y_GAP);
                err=esp_lcd_panel_swap_xy(panel,geometry.swap_xy);
                if (err!=ESP_OK) break;
                err=esp_lcd_panel_mirror(panel,geometry.mirror_x,geometry.mirror_y);
                if (err!=ESP_OK) break;
                err=esp_lcd_panel_set_gap(panel,geometry.x_gap,geometry.y_gap);
                if (err!=ESP_OK) break;
                applied_rotation=ui.rotation;previous_valid=false;
            }
            bands=previous_valid?ui_render_dirty(&previous.model,&previous.snapshot,previous.save_failed,&previous.pose,
                &frame.model,&frame.snapshot,frame.save_failed,&frame.pose):ui_render_all_bands(&ui);
            next_frame=now+(animating?UI_ANIMATION_FRAME_MS:UI_IDLE_FRAME_MS);
            requested=false;
            if(bands){
                rendering=true;
#ifdef CAN_PANEL_BENCHMARK
                benchmark_start=esp_timer_get_time();benchmark_bands=__builtin_popcount(bands);
#endif
            }
            else previous=frame;
        }
        if(rendering){
            if(bands){
                unsigned band=__builtin_ctz(bands);
                int top=band*(UI_DMA_PIXELS/ui_render_width(&frame.model));
                // Draw into the other buffer while the previous band is on SPI.
                // The pose and snapshot stay frozen until the whole frame ends.
                ui_render_animated_band(pixels[buffer],top,&frame.model,&frame.snapshot,frame.save_failed,&frame.pose);
                if(transfer_pending){
                    if(!ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(100))){err=ESP_ERR_TIMEOUT;break;}
                    transfer_pending=false;
                }
                int rows=ui_render_rows(&frame.model,top);
                err=esp_lcd_panel_draw_bitmap(panel,0,top,ui_render_width(&frame.model),top+rows,pixels[buffer]);
                if(err!=ESP_OK)break;
                transfer_pending=true;buffer^=1;bands&=~(1u<<band);
                // DMA supplies the wait; a delay per band would stretch one
                // animation frame by over 200ms. Keys are sampled again now.
                continue;
            }
            if(transfer_pending){
                if(!ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(100))){err=ESP_ERR_TIMEOUT;break;}
                transfer_pending=false;
            }
            previous=frame;previous_valid=true;rendering=false;
#ifdef CAN_PANEL_BENCHMARK
            if(benchmark_action)printf("PANEL_FRAME page=%u pose=%d us=%"PRId64" bands=%u\n",
                frame.model.page,frame.pose.page,esp_timer_get_time()-benchmark_start,benchmark_bands);
#endif
            if(!panel_ready){
                err=esp_lcd_panel_disp_on_off(panel,true);
                if(err!=ESP_OK)break;
                panel_ready=true;
            }
            continue;
        }
        vTaskDelay(1);
    }
    // Retain both DMA buffers/handles on failure: SPI may still own one.
    backlight(0); board_buzzer_stop();
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
