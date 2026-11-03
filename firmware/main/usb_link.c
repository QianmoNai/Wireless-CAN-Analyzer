#include "usb_link.h"
#include "usb_protocol.h"
#include "ack_delivery.h"
#include "uart_bridge.h"
#include "network_link.h"
#include "settings_protocol.h"
#include "sdkconfig.h"
#if CONFIG_IDF_TARGET_ESP32S3
#include "driver/usb_serial_jtag.h"
#include "freertos/semphr.h"
#include "esp_timer.h"
#include <stdatomic.h>

typedef struct {uint32_t session;bridge_packet_t packet;} queued_t;
static QueueHandle_t raw_out,control_out;
static SemaphoreHandle_t guard;
static atomic_uint session_id,last_ping;
static usb_decoder_t decoder;
static uint32_t millis(void){return (uint32_t)(esp_timer_get_time()/1000);}
bool usb_link_connected(void){return network_usb_mode()&&atomic_load(&session_id)&&
    (uint32_t)(millis()-atomic_load(&last_ping))<4000&&usb_serial_jtag_is_connected();}
static void discard_queue(QueueHandle_t queue){
    queued_t q;
    while(xQueueReceive(queue,&q,0)==pdTRUE)uart_bridge_drop_packet(&q.packet);
}
// The same guard serializes mode changes, command submission and session resets.
static void clear_session(void){
    atomic_store(&session_id,0);decoder.used=0;
    discard_queue(raw_out);discard_queue(control_out);
}
void usb_link_reset(void){
    if(!guard)return;
    xSemaphoreTake(guard,portMAX_DELAY);clear_session();xSemaphoreGive(guard);
}
bool usb_link_send(const bridge_packet_t *p){
    if(!usb_link_connected())return false;
    queued_t q={.session=atomic_load(&session_id),.packet=*p};
    return xQueueSend(bridge_packet_is_raw(p)?raw_out:control_out,&q,0)==pdTRUE;
}
static bool send_packet(uint32_t session,const uint8_t payload[20]){
    uint8_t frame[32];usb_encode(frame,session,payload);
    // A partial write is fatal to this session. Never replay a command.
    return usb_serial_jtag_write_bytes(frame,sizeof(frame),pdMS_TO_TICKS(20))==sizeof(frame);
}
static bool incoming(uint32_t session,uint8_t p[20]){
    if(!session||p[0]!=1)return true;
    if(p[1]==SETTINGS_COMMAND){
        // A physical bootstrap can only query or select USB. It never grants
        // a data session, and an existing session still excludes other IDs.
        if(atomic_load(&session_id)&&session!=atomic_load(&session_id))return true;
        bool active=session==atomic_load(&session_id)&&usb_link_connected();
        bridge_packet_t reply;
        int status=(!active&&!settings_usb_bootstrap(p))?0x13:ble_bridge_settings_command(p,&reply);
        uint8_t ack[20]={1,18,(uint8_t)status,p[1]};memcpy(ack+4,p+16,4);
        return send_packet(session,ack)&&(status||send_packet(session,reply.bytes));
    }
    if(!network_usb_mode())return true;
    uint8_t hello[20]={1,USB_HELLO};memcpy(hello+4,"CANUSB1",7);
    if(!memcmp(p,hello,20)){
        if(session!=atomic_load(&session_id)){
            clear_session();ble_bridge_reset_session();
            atomic_store(&last_ping,millis());atomic_store(&session_id,session);
        }
        atomic_store(&last_ping,millis());hello[2]=1;
        return send_packet(session,hello);
    }
    if(session!=atomic_load(&session_id)||!usb_link_connected())return true;
    if(p[1]==USB_BYE){clear_session();ble_bridge_reset_session();return true;}
    if(p[1]==USB_PING){atomic_store(&last_ping,millis());return send_packet(session,p);}
    int status=ble_bridge_command(p);
    uint8_t ack[20]={1,18,(uint8_t)status,p[1]};memcpy(ack+4,p+16,4);
    return send_packet(session,ack);
}
static void service(const uint8_t *bytes,int n){
    xSemaphoreTake(guard,portMAX_DELAY);
    bool ok=true;
    if(!network_usb_mode()&&atomic_load(&session_id))clear_session();
    {
        if(atomic_load(&session_id)&&!usb_link_connected()){clear_session();ble_bridge_reset_session();}
        for(int i=0;i<n&&ok;i++){
            uint32_t session;uint8_t payload[20];
            if(usb_decode(&decoder,bytes[i],&session,payload))ok=incoming(session,payload);
        }
        queued_t q;
        for(unsigned count=0;ok&&count<32&&usb_link_connected()&&
            (xQueueReceive(control_out,&q,0)==pdTRUE||xQueueReceive(raw_out,&q,0)==pdTRUE);count++)
        {
            if(q.session==atomic_load(&session_id))ok=send_packet(q.session,q.packet.bytes);
            // A partial CUSB frame has no usable payload. Accepted queued UART
            // packets are owned here; queue rejection is owned by the caller.
            if(!ok || q.session!=atomic_load(&session_id))uart_bridge_drop_packet(&q.packet);
        }
        if(!ok){clear_session();ble_bridge_reset_session();}
    }
    xSemaphoreGive(guard);
}
static void worker(void *arg){
    (void)arg;
    for(;;){
        uint8_t bytes[256];int n=usb_serial_jtag_read_bytes(bytes,sizeof(bytes),pdMS_TO_TICKS(10));
        service(bytes,n);
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
void usb_link_init(void){
    guard=xSemaphoreCreateMutex();raw_out=xQueueCreate(128,sizeof(queued_t));control_out=xQueueCreate(32,sizeof(queued_t));
    ESP_ERROR_CHECK(guard&&raw_out&&control_out?ESP_OK:ESP_ERR_NO_MEM);
    usb_serial_jtag_driver_config_t cfg={.rx_buffer_size=4096,.tx_buffer_size=8192};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    ESP_ERROR_CHECK(xTaskCreate(worker,"usb_bridge",4096,NULL,4,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
}
#else
void usb_link_init(void){}
void usb_link_reset(void){}
bool usb_link_connected(void){return false;}
bool usb_link_send(const bridge_packet_t *p){(void)p;return false;}
#endif
