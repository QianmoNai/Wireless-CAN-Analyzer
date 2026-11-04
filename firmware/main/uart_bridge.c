#include "uart_bridge.h"
#include "dual_link.h"
#include "network_link.h"
#include "wifi_uart_diag.h"
#include <stdatomic.h>
#include <string.h>
#include "sdkconfig.h"
#if CONFIG_IDF_TARGET_ESP32S3
#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
// The board's TX/RX labels are U0TXD/U0RXD. UART1 is routed onto these pads;
// the application console is disabled; these pads carry only bridged data.
enum { PORT=UART_NUM_1, TX_PIN=43, RX_PIN=44 };
typedef struct { bridge_packet_t packet; uint32_t generation,config; } uart_command_t;
static QueueHandle_t commands,events;
static atomic_uint config_request=115200u<<1, connection_generation;
static atomic_uint rx_total,tx_total,drop_total,error_total;
static void count_drop(unsigned n,unsigned cause){
    atomic_fetch_add(&drop_total,n);
    wifi_diag_add(WD_UART_DROP_BYTES,n,WD_NOW_SEQUENCE);
    if(cause<WD_FIELDS)wifi_diag_add(cause,n,WD_NOW_SEQUENCE);
}
#if CONFIG_BLE_TEST_DIAGNOSTICS
static atomic_uint ud[UD_WORDS];
#define ud_add(word,n) atomic_fetch_add(&ud[word],n)
void uart_bridge_diagnostics(uint32_t out[UD_WORDS]) {
    for(unsigned i=0;i<UD_WORDS;i++)out[i]=atomic_load(&ud[i]);
}
static void ud_event_depth(void) {
    unsigned n=uxQueueMessagesWaiting(events),old=atomic_load(&ud[UD_EVENT_HIGH_WATER]);
    while(old<n&&!atomic_compare_exchange_weak(&ud[UD_EVENT_HIGH_WATER],&old,n)){}
}
#else
#define ud_add(word,n) ((void)0)
#define ud_event_depth() ((void)0)
#endif
static void flush_rx(bool config_change) {
    size_t buffered=0;
    int rc=uart_get_buffered_data_len(PORT,&buffered);
    ud_add(UD_BUFFER_QUERY_ERRORS,rc!=ESP_OK);
    ud_add(config_change?UD_CONFIG_FLUSHES:UD_ERROR_FLUSHES,1);
    ud_add(config_change?UD_CONFIG_FLUSH_BYTES:UD_ERROR_FLUSH_BYTES,buffered);
    count_drop(buffered,WD_CONFIG_FLUSH_BYTES);
    rc=uart_flush_input(PORT);
    ud_add(UD_FLUSH_ERRORS,rc!=ESP_OK);
    (void)rc;
    (void)config_change;
}
static atomic_bool ready;
static TaskHandle_t worker,tx_worker;
// Serializes driver lifetime and pending TX state, including the completion wait.
static SemaphoreHandle_t port_lock;
static bool installed;
static uint32_t applied_config,applied_generation,peer_generation,rx_sequence;
static uint8_t tx_data[DUAL_UART_CHUNK];
static size_t tx_size,tx_offset;
static bridge_packet_t tx_command;
static bool tx_app;
// Bound same-turn dual TX work even if every queued block completes immediately.
enum { DUAL_TX_STEPS_PER_TURN=4 };
static void result(const bridge_packet_t *command,unsigned status) {
    bridge_packet_t p=*command;p.bytes[1]=UART_TX_EVENT;p.bytes[3]=p.bytes[2];p.bytes[2]=status;
    ble_bridge_publish(&p);
}
static void discard_pending(bool reply) {
    if(tx_size){ud_add(UD_TX_DISCARD_BYTES,tx_size-tx_offset);count_drop(tx_size-tx_offset,WD_TX_DISCARD_BYTES);if(tx_app&&reply)result(&tx_command,3);}
    tx_size=tx_offset=0;tx_app=false;
    uart_command_t command;
    while(xQueueReceive(commands,&command,0)==pdTRUE){
        ud_add(UD_TX_DISCARD_BYTES,command.packet.bytes[2]);count_drop(command.packet.bytes[2],WD_TX_DISCARD_BYTES);
        if(reply && command.generation==atomic_load(&connection_generation))result(&command.packet,3);
    }
}
static void close_port(void) {
    atomic_store(&ready,false);
    if(installed){uart_driver_delete(PORT);installed=false;events=NULL;}
}
static bool open_port(uint32_t baud) {
    uart_config_t c={.baud_rate=baud,.data_bits=UART_DATA_8_BITS,.parity=UART_PARITY_DISABLE,
        .stop_bits=UART_STOP_BITS_1,.flow_ctrl=UART_HW_FLOWCTRL_DISABLE,.source_clk=UART_SCLK_DEFAULT};
    if(uart_param_config(PORT,&c)!=ESP_OK || uart_set_pin(PORT,TX_PIN,RX_PIN,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE)!=ESP_OK)return false;
    if(uart_driver_install(PORT,4096,0,32,&events,0)!=ESP_OK)return false;
    installed=true;uart_set_rx_timeout(PORT,2);atomic_store(&ready,true);return true;
}
static void publish_status(void) {
    uart_bridge_stats_t s;uart_bridge_stats(&s);
    bridge_packet_t p={.bytes={1,UART_STATUS_EVENT,uart_bridge_selected(),s.ready?0:1}};
    wire_put_u32(p.bytes+4,s.baud);wire_put_u32(p.bytes+8,s.rx);wire_put_u32(p.bytes+12,s.tx);wire_put_u32(p.bytes+16,s.dropped);
    ble_bridge_publish(&p);
}
// Caller holds port_lock. A true result requests another bounded advancement.
static bool tx_advance(bool dual,unsigned wait) {
    uint32_t config=applied_config,epoch=applied_generation,peer=peer_generation;
    if(!(config&1) || !installed)return false;
    // Recheck cancellation before dequeuing each subsequent dual block.
    if(dual && (config!=atomic_load(&config_request) ||
       epoch!=atomic_load(&connection_generation) ||
       peer!=dual_link_generation() || !dual_link_connected()))return false;
    if(!tx_size){
        if(dual){dual_link_uart_take(tx_data,&tx_size);tx_app=false;}
        else {
            uart_command_t command;
            if(xQueueReceive(commands,&command,0)==pdTRUE){
                if(command.generation!=epoch || command.config!=config){ud_add(UD_TX_DISCARD_BYTES,command.packet.bytes[2]);count_drop(command.packet.bytes[2],WD_TX_DISCARD_BYTES);return false;}
                tx_command=command.packet;tx_size=command.packet.bytes[2];memcpy(tx_data,command.packet.bytes+4,tx_size);tx_app=true;
            }
        }
        tx_offset=0;
    }
    // Recheck selection before putting additional bytes into the FIFO.
    if(tx_size && config==atomic_load(&config_request) && epoch==atomic_load(&connection_generation) &&
       (!dual || (peer==dual_link_generation() && dual_link_connected()))){
        if(tx_offset<tx_size){
            int n=uart_tx_chars(PORT,(const char*)tx_data+tx_offset,tx_size-tx_offset);
            if(n<0){
                ud_add(UD_TX_DISCARD_BYTES,tx_size-tx_offset);
                atomic_fetch_add(&error_total,1);count_drop(tx_size-tx_offset,WD_TX_DISCARD_BYTES);
                if(tx_app)result(&tx_command,2);
                tx_size=tx_offset=0;tx_app=false;return dual;
            }
            if(n>0)tx_offset+=n;
        }
        if(dual || tx_offset==tx_size){
            int done=uart_wait_tx_done(PORT,wait);
            if(done==ESP_OK){
                if(tx_offset==tx_size){
                    atomic_fetch_add(&tx_total,tx_size);
                    if(tx_app)result(&tx_command,0);
                    tx_size=tx_offset=0;tx_app=false;
                }
                return dual;
            }
            if(dual && done!=ESP_ERR_TIMEOUT){
                atomic_fetch_add(&error_total,1);
                discard_pending(false);
                return true;
            }
            // A timeout only bounds cancellation latency; normal progress
            // wakes on the driver's TX_DONE ISR semaphore, not a tick.
            return dual;
        }
    }
    // No queued data (or the unchanged non-dual nonblocking path).
    return false;
}
static void step(void) {
    uint32_t config=atomic_load(&config_request),epoch=atomic_load(&connection_generation);
    uint32_t peer=dual_link_generation();
    if(config!=applied_config || epoch!=applied_generation || peer!=peer_generation) {
        xSemaphoreTake(port_lock,portMAX_DELAY);
        bool reopen=config!=applied_config;
        discard_pending(epoch==applied_generation);
        if(installed)flush_rx(true);
        if(reopen){close_port();if(config&1 && !open_port(config>>1))atomic_fetch_add(&error_total,1);}
        applied_config=config;applied_generation=epoch;peer_generation=peer;
        if(tx_worker)xTaskNotifyGive(tx_worker);
        xSemaphoreGive(port_lock);
    }
    if(!(config&1) || !installed)return;
    size_t buffered=0;
    if(network_wifi_mode()&&uart_get_buffered_data_len(PORT,&buffered)==ESP_OK)
        wifi_diag_max(WD_RING_HIGH_BYTES,buffered); // Sampled readable ring, not ISR/FIFO/stash occupancy.
    uart_event_t event;
    ud_event_depth();
    for(unsigned i=0;i<32&&xQueueReceive(events,&event,0)==pdTRUE;i++){
        if(event.type==UART_DATA){ud_add(UD_DATA_EVENTS,1);ud_add(UD_DATA_EVENT_BYTES,event.size);}
        if(event.type==UART_FIFO_OVF){ud_add(UD_FIFO_OVF,1);wifi_diag_add(WD_FIFO_OVERRUN_EVENTS,1,WD_NOW_SEQUENCE);}
        if(event.type==UART_BUFFER_FULL){ud_add(UD_BUFFER_FULL,1);wifi_diag_add(WD_RING_FULL_EVENTS,1,WD_NOW_SEQUENCE);}
        if(event.type==UART_FRAME_ERR)ud_add(UD_FRAME_ERR,1);
        if(event.type==UART_PARITY_ERR)ud_add(UD_PARITY_ERR,1);
        if(event.type==UART_FIFO_OVF || event.type==UART_BUFFER_FULL || event.type==UART_FRAME_ERR || event.type==UART_PARITY_ERR){
            atomic_fetch_add(&error_total,1);
            // The IDF ISR already resets an overflowing hardware FIFO. Keep
            // readable ring/stash data: another flush needlessly destroys it
            // (and races new FIFO arrivals that buffered_data_len cannot count).
            // BUFFER_FULL is recovered by uart_read_bytes draining the stash.
            // Error events have no lost-byte count; never invent one here.
        }
    }
    bool dual=network_dual_mode();
    bool connected=dual?dual_link_connected():ble_bridge_connected();
    uint8_t data[DUAL_UART_CHUNK];
    for(unsigned i=0;i<64;i++){
        int n=uart_read_bytes(PORT,data,dual?sizeof(data):UART_WIRE_BYTES,0);
        ud_add(UD_READ_CALLS,1);
        if(n<=0){ud_add(UD_READ_ERRORS,n<0);if(n<0)wifi_diag_add(WD_UART_READ_ERRORS,1,WD_NOW_SEQUENCE);break;}
        ud_add(UD_READ_BYTES,n);
        atomic_fetch_add(&rx_total,n);
        wifi_diag_add(WD_UART_RX_BYTES,n,WD_NOW_SEQUENCE);
        if(!connected){ud_add(UD_DISCONNECTED_BYTES,n);count_drop(n,WD_DISCONNECTED_BYTES);continue;}
        if(dual){ud_add(UD_DUAL_BYTES,n);if(!dual_link_uart_rx(data,n))count_drop(n,WD_FIELDS);}
        else {
            bridge_packet_t p={.bytes={1,UART_RX_EVENT,n,0}};
            memcpy(p.bytes+4,data,n);wire_put_u32(p.bytes+16,++rx_sequence);
            wifi_diag_set(WD_UART_SEQUENCE,rx_sequence,rx_sequence);
            ud_add(UD_BLE_ATTEMPT_BYTES,n);
            if(!ble_bridge_uart_publish(&p)){ud_add(UD_BLE_REJECT_BYTES,n);count_drop(n,WD_FIELDS);}
            else ud_add(UD_BLE_ACCEPT_BYTES,n);
        }
    }
    if(!connected || !dual){
        xSemaphoreTake(port_lock,portMAX_DELAY);
        if(!connected)discard_pending(false);
        else (void)tx_advance(false,0);
        xSemaphoreGive(port_lock);
    }
}
// RX/status retain their cadence and can drain RX during a dual TX wait.
static bool dual_tx_turn(void) {
    for(unsigned i=0;i<DUAL_TX_STEPS_PER_TURN;i++){
        xSemaphoreTake(port_lock,portMAX_DELAY);
        bool more=network_dual_mode() && tx_advance(true,pdMS_TO_TICKS(20));
        xSemaphoreGive(port_lock);
        if(!more)return false;
    }
    return true;
}
static void dual_tx_iteration(void) {
    int64_t started=esp_timer_get_time();
    if(!dual_tx_turn())ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
    // A real completion wait already allows the idle task to run. Only a
    // full budget of immediate completions/no progress needs a bounded delay.
    else if(esp_timer_get_time()-started < (int64_t)portTICK_PERIOD_MS*1000)vTaskDelay(1);
    else taskYIELD();
}
static void dual_tx_task(void *arg) {
    (void)arg;
    for(;;)dual_tx_iteration();
}

static void task(void *arg) {
    (void)arg;uint32_t last_status=0;
    for(;;){
        step();uint32_t now=esp_timer_get_time()/1000;
        if((uint32_t)(now-last_status)>=500){publish_status();last_status=now;}
        ulTaskNotifyTake(pdTRUE,1);
    }
}
void uart_bridge_init(void) {
    commands=xQueueCreate(32,sizeof(uart_command_t));
    port_lock=xSemaphoreCreateMutex();
    if(commands && port_lock &&
       xTaskCreate(dual_tx_task,"uart_dual_tx",3072,NULL,4,&tx_worker)==pdPASS){
        dual_link_set_uart_owner(tx_worker);
        xTaskCreate(task,"uart_bridge",4096,NULL,4,&worker);
    }
}
void uart_bridge_configure(bool selected,uint32_t baud) {
    if(baud!=9600&&baud!=19200&&baud!=38400&&baud!=57600&&baud!=115200&&baud!=230400&&baud!=460800&&baud!=921600)return;
    uint32_t wanted=(baud<<1)|selected;
    if(atomic_exchange(&config_request,wanted)!=wanted){
        dual_link_set_uart(selected);dual_link_reset_data();
        if(worker)xTaskNotifyGive(worker);
        if(tx_worker)xTaskNotifyGive(tx_worker);
    }
}
bool uart_bridge_selected(void){return atomic_load(&config_request)&1;}
bool uart_bridge_send(const uint8_t p[20]) {
    if(!worker || !atomic_load(&ready) || !uart_bridge_selected() || !uart_command_valid(p))return false;
    uart_command_t c={.generation=atomic_load(&connection_generation),.config=atomic_load(&config_request)};
    memcpy(c.packet.bytes,p,20);
    bool ok=xQueueSend(commands,&c,0)==pdTRUE;if(ok)xTaskNotifyGive(worker);return ok;
}
void uart_bridge_disconnect(void){atomic_fetch_add(&connection_generation,1);if(worker)xTaskNotifyGive(worker);if(tx_worker)xTaskNotifyGive(tx_worker);}
void uart_bridge_drop_packet(const bridge_packet_t *p){
    if(p->bytes[0]==1 && p->bytes[1]==UART_RX_EVENT && p->bytes[2]>=1 &&
       p->bytes[2]<=UART_WIRE_BYTES && !p->bytes[3]){
        ud_add(UD_DOWNSTREAM_DROP_BYTES,p->bytes[2]);
        atomic_fetch_add(&drop_total,p->bytes[2]);
        wifi_diag_drop(WD_UART_DROP_BYTES,p);
    }
}
void uart_bridge_stats(uart_bridge_stats_t *s){
    *s=(uart_bridge_stats_t){.rx=atomic_load(&rx_total),.tx=atomic_load(&tx_total),.dropped=atomic_load(&drop_total)+dual_link_uart_dropped(),
        .errors=atomic_load(&error_total),.baud=atomic_load(&config_request)>>1,.ready=atomic_load(&ready)};
}
#else
#if CONFIG_BLE_TEST_DIAGNOSTICS
void uart_bridge_diagnostics(uint32_t out[UD_WORDS]){memset(out,0,UD_WORDS*sizeof(*out));}
#endif
void uart_bridge_init(void){}
void uart_bridge_configure(bool selected,uint32_t baud){(void)selected;(void)baud;}
bool uart_bridge_selected(void){return false;}
bool uart_bridge_send(const uint8_t p[20]){(void)p;return false;}
void uart_bridge_disconnect(void){}
void uart_bridge_drop_packet(const bridge_packet_t *p){(void)p;}
void uart_bridge_stats(uart_bridge_stats_t *s){memset(s,0,sizeof(*s));}
#endif
