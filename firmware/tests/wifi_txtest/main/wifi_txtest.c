/* Separate open SoftAP TX-path experiment. No normal firmware code is linked. */
#include "sdkconfig.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#if !CONFIG_IDF_TARGET_ESP32S3 || CONFIG_BT_ENABLED || CONFIG_ESP_WIFI_NVS_ENABLED || CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE || CONFIG_ESP_PHY_INIT_DATA_IN_PARTITION
#error "TX test requires ESP32-S3, no Bluetooth, and no Wi-Fi/PHY NVS access"
#endif

static const char *TAG="wifi_txtest";
#define MAC_FMT "%02x:%02x:%02x:%02x:%02x:%02x"
#define MAC_ARGS(m) (m)[0],(m)[1],(m)[2],(m)[3],(m)[4],(m)[5]

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;(void)base;
    if(id==WIFI_EVENT_AP_START)ESP_LOGI(TAG,"AP started");
    else if(id==WIFI_EVENT_AP_STACONNECTED){
        const wifi_event_ap_staconnected_t *e=data;
        ESP_LOGI(TAG,"STA join MAC="MAC_FMT" AID=%u",MAC_ARGS(e->mac),e->aid);
    }else if(id==WIFI_EVENT_AP_STADISCONNECTED){
        const wifi_event_ap_stadisconnected_t *e=data;
        ESP_LOGI(TAG,"STA leave MAC="MAC_FMT" AID=%u reason=%u",MAC_ARGS(e->mac),e->aid,e->reason);
    }else if(id==WIFI_EVENT_AP_PROBEREQRECVED){
        /* Rate-limit probes; no SSID, frame contents, credentials or buffers. */
        static int64_t last=-1000000;
        int64_t now=esp_timer_get_time();
        if(now-last>=1000000){
            const wifi_event_ap_probe_req_rx_t *e=data;last=now;
            ESP_LOGI(TAG,"probe MAC="MAC_FMT" RSSI=%d",MAC_ARGS(e->mac),e->rssi);
        }
    }
}

static void status(void)
{
    uint8_t ap[6]={0},sta[6]={0},channel=0;
    wifi_second_chan_t second;wifi_country_t country={0};
    wifi_sta_list_t stations={0};int8_t power=0;
    esp_err_t a=esp_wifi_get_mac(WIFI_IF_AP,ap),s=esp_wifi_get_mac(WIFI_IF_STA,sta);
    esp_err_t c=esp_wifi_get_channel(&channel,&second),p=esp_wifi_get_max_tx_power(&power);
    esp_err_t r=esp_wifi_get_country(&country),n=esp_wifi_ap_get_sta_list(&stations);
    ESP_LOGI(TAG,"AP_MAC="MAC_FMT" STA_MAC="MAC_FMT,MAC_ARGS(ap),MAC_ARGS(sta));
    ESP_LOGI(TAG,"ms=%"PRId64" channel=%u tx_qdbm=%d tx_cap_dBm=%.2f country=%.2s stations=%d",
             esp_timer_get_time()/1000,channel,power,power/4.0,country.cc,stations.num);
    if(a||s||c||p||r||n)ESP_LOGW(TAG,"getter errors: ap=%d sta=%d channel=%d tx=%d country=%d stations=%d",a,s,c,p,r,n);
}

void app_main(void)
{
    ESP_LOGI(TAG,"SEPARATE TX TEST; IDF=%s reset_reason=%d",esp_get_idf_version(),esp_reset_reason());
    ESP_LOGI(TAG,"NVS never initialized; Wi-Fi storage RAM; PHY full calibration, no calibration NVS reads/writes");
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_create_default_wifi_ap()?ESP_OK:ESP_ERR_NO_MEM);
    wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();init.nvs_enable=0;
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    uint8_t mac[6];ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_AP,mac));
    wifi_config_t config={0};
    snprintf((char *)config.ap.ssid,sizeof(config.ap.ssid),"CANScope-TX-%02X%02X",mac[4],mac[5]);
    config.ap.ssid_len=strlen((char *)config.ap.ssid);
    config.ap.channel=1;config.ap.authmode=WIFI_AUTH_OPEN;
    config.ap.max_connection=4;config.ap.pmf_cfg.required=false;
    ESP_LOGI(TAG,"SSID=%s OPEN channel=1 HT20; local DHCP only, no uplink/NAT",config.ap.ssid);
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP,&config));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_AP,WIFI_BW20));
    uint32_t mask;ESP_ERROR_CHECK(esp_wifi_get_event_mask(&mask));
    ESP_ERROR_CHECK(esp_wifi_set_event_mask(mask&~WIFI_EVENT_MASK_AP_PROBEREQRECVED));
    ESP_ERROR_CHECK(esp_wifi_start());
    /* IDF maps the maximum request 84 to its supported/regulatory cap, often 80. */
    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(WIFI_TXTEST_POWER_QDBM));
    ESP_LOGI(TAG,"requested TX power=%d quarter-dBm units; read-back below is authoritative",WIFI_TXTEST_POWER_QDBM);
    for(;;){status();vTaskDelay(pdMS_TO_TICKS(5000));}
}
