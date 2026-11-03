#include "ble_bridge.h"
#include "network_link.h"
#include "usb_link.h"
#include "uart_bridge.h"
#include "device_settings.h"
#include "esp_random.h"
void ble_store_config_init(void);
#include "home_status.h"
#include "ack_delivery.h"
#include "notify_resource.h"
#include "esp_timer.h"
#include <stdatomic.h>
#include <string.h>
#include "esp_log.h"
#include "nvs_flash.h"
#include "name_store.h"
#include "bridge_name.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_sm.h"
#include "host/ble_store.h"
#include "host/util/util.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define UUID_BYTES(n) 0x23,0xd1,0xbc,0xea,0x5f,0x78,0x23,0x15,0xde,0xef,0x12,0x12,n,0x00,0xde,0xc0
static const ble_uuid128_t service_uuid = BLE_UUID128_INIT(UUID_BYTES(1));
static const ble_uuid128_t write_uuid = BLE_UUID128_INIT(UUID_BYTES(2));
static const ble_uuid128_t notify_uuid = BLE_UUID128_INIT(UUID_BYTES(3));
static const ble_uuid128_t network_uuid = BLE_UUID128_INIT(UUID_BYTES(4));
static const char *TAG = "can_ble";
static QueueHandle_t command_queue, query_command_queue, ack_notify_queue, notify_queue, control_notify_queue;
static uint16_t notify_handle;
static home_status_t home_status;
static portMUX_TYPE home_lock=portMUX_INITIALIZER_UNLOCKED;
// Called with home_lock held. Explicit command acknowledgments always pass.
static bool home_redundant(const bridge_packet_t *p){
 uint8_t mode=home_status.mode,type=p->bytes[1];
 if(type==UART_RX_EVENT)return !uart_bridge_selected();
 if(uart_bridge_selected()&&(type==1||type==11||type==12||type==13||type==14||type==16))return true;
 if(type==16)return mode!=1||wire_u32(p->bytes+16)!=home_status.revision;
 if(type==13||type==14||type==15)return mode!=0;
 if(type==12)return mode!=2||!home_status.sample_hz||wire_u32(p->bytes+16)!=home_status.revision;
 if(type==11)return mode!=2||wire_u32(p->bytes+4)!=home_status.revision;
 if(!mode)return false;
 if(type<=3||(type==5&&wire_u32(p->bytes+16)==0)||(type==7&&p->bytes[2]==0))return true;
 if(mode>=2&&(type==4||type==9))return true;
 if(type==8&&wire_u32(p->bytes+16)==0){
   // Terminal query errors remain essential even for hidden channels.
   if(p->bytes[2]!=0)return false;
   if(mode==3)return true;
   if(mode==2&&wire_u32(p->bytes+4)!=home_status.motors[home_status.target].id)return true;
 }
 return false;
}
static bool suppress_packet(const bridge_packet_t *p){portENTER_CRITICAL(&home_lock);bool drop=home_redundant(p);portEXIT_CRITICAL(&home_lock);return drop;}
static void reset_home(void){portENTER_CRITICAL(&home_lock);memset(&home_status,0,sizeof(home_status));portEXIT_CRITICAL(&home_lock);}
static uint8_t address_type;
static atomic_int connection = BLE_HS_CONN_HANDLE_NONE;
static atomic_bool subscribed;
static atomic_uint dropped;
static atomic_bool motor_busy, stop_requested;
static void advertise(void);
static portMUX_TYPE name_lock = portMUX_INITIALIZER_UNLOCKED;
static char saved_name[BRIDGE_NAME_MAX + 1] = "ESP32-CAN";
static bridge_packet_t name_status(uint8_t result) {
    bridge_packet_t packet = {.bytes = {1, 7, result, 0}};
    portENTER_CRITICAL(&name_lock);
    packet.bytes[3] = strlen(saved_name);
    memcpy(packet.bytes + 4, saved_name, packet.bytes[3]);
    portEXIT_CRITICAL(&name_lock);
    return packet;
}
static void set_saved_name(const char *name) {
    portENTER_CRITICAL(&name_lock);
    memcpy(saved_name, name, strlen(name) + 1);
    portEXIT_CRITICAL(&name_lock);
}
static void rename_device(const uint8_t *bytes, uint8_t len) {
    char name[BRIDGE_NAME_MAX + 1] = {0};
    memcpy(name, bytes, len);
    esp_err_t err = bridge_name_save(name);
    if (err == ESP_OK) {
        set_saved_name(name);
        if (ble_svc_gap_device_name_set(name) != 0) err = ESP_FAIL;
    }
    bridge_packet_t reply = name_status(err == ESP_OK ? 1 : 2);
    ble_bridge_publish(&reply);
}

int ble_bridge_settings_command(const uint8_t p[20],bridge_packet_t *reply) {
    if(atomic_load(&motor_busy))return BLE_ATT_ERR_INSUFFICIENT_RES;
    return device_settings_command(p,reply);
}

int ble_bridge_command(const uint8_t bytes[20])
{
    if(network_dual_mode())return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    bridge_packet_t packet;memcpy(packet.bytes,bytes,20);
    uint8_t *p = packet.bytes;
    if (p[0] != 1) return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    if(p[1]==SETTINGS_COMMAND){
        bridge_packet_t reply;int result=ble_bridge_settings_command(p,&reply);
        if(!result)ble_bridge_publish(&reply);
        return result;
    }
    if(p[1]==UART_SEND_COMMAND){
        if(!uart_command_valid(p) || !uart_bridge_selected())return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
        return uart_bridge_send(p)?0:BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if(uart_bridge_selected() && (p[1]==0x10||p[1]==0x11||p[1]==0x12||p[1]==0x20||
        (p[1]==0x14 && wire_u32(p+8))))return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    if(p[1]==0x16||p[1]==0x17){
        bridge_packet_t ack={0};
        portENTER_CRITICAL(&home_lock);bool ok=home_command(&home_status,&packet,&ack);portEXIT_CRITICAL(&home_lock);
        if(!ok)return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
        if(p[1]==0x17){xQueueReset(notify_queue);ble_bridge_publish(&ack);}
        return 0;
    }
    if (p[1] == 0x21) {
        atomic_store(&stop_requested, true);
        return 0;
    }
    if (atomic_load(&motor_busy) && !(p[1]==0x15 || (p[1]==0x14 && wire_u32(p+8)==0)))
        return BLE_ATT_ERR_INSUFFICIENT_RES;
    if (p[1] == 0x13) {
        if (p[3] != 0 || !bridge_name_valid(p + 4, p[2])) return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
        atomic_store(&stop_requested, true);
        rename_device(p + 4, p[2]);
        return 0;
    }
    if (p[1] == 0x14 || p[1] == 0x15) {
        if(p[2] || p[3] || wire_u32(p+12)) return BLE_ATT_ERR_UNLIKELY;
        uint32_t id=wire_u32(p+4), hz=wire_u32(p+8);
        if(p[1]==0x14 ? (!id || id>0x7fe || hz>1000) : (id || hz)) return BLE_ATT_ERR_UNLIKELY;
    } else if (p[1] == 0x10) {
        if ((p[2] & ~3) || p[3] > 8 ||
            wire_u32(p + 4) > ((p[2] & 1) ? 0x1fffffffU : 0x7ffU))
            return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    } else if (p[1] == 0x11) {
        if (p[2] > 1) return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    } else if (p[1] == 0x12) {
        uint32_t rate = wire_u32(p + 4);
        if (rate != 125000 && rate != 250000 && rate != 500000 && rate != 1000000)
            return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    } else if (p[1] == 0x20) {
        if ((p[2] != 0x40 && p[2] != 0x80) || wire_u32(p + 4) > 0x7ff ||
            (p[2] == 0x40 ? p[3] != 8 : (p[3] != 4 && p[3] != 8)))
            return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    } else return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    QueueHandle_t destination = (p[1]==0x14 || p[1]==0x15) ? query_command_queue : command_queue;
    if (xQueueSend(destination, &packet, 0) != pdTRUE) return BLE_ATT_ERR_INSUFFICIENT_RES;
    if (p[1] == 0x20) atomic_store(&motor_busy, true);
    return 0;
}

void ble_bridge_reset_session(void){
    reset_home();uart_bridge_disconnect();atomic_store(&stop_requested,true);
    if(notify_queue)xQueueReset(notify_queue);
    if(control_notify_queue)xQueueReset(control_notify_queue);
    if(ack_notify_queue)xQueueReset(ack_notify_queue);
    if(command_queue)xQueueReset(command_queue);
    if(query_command_queue)xQueueReset(query_command_queue);
}
static int access_cb(uint16_t conn,uint16_t attr,struct ble_gatt_access_ctxt *ctx,void *arg){
    if(ctx->op!=BLE_GATT_ACCESS_OP_WRITE_CHR)return BLE_ATT_ERR_READ_NOT_PERMITTED;
    uint8_t packet[20];if(OS_MBUF_PKTLEN(ctx->om)!=20||ble_hs_mbuf_to_flat(ctx->om,packet,20,NULL))return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    if(network_wifi_mode()||network_dual_mode()||network_usb_mode())return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    return ble_bridge_command(packet);
}
static int network_access(uint16_t conn,uint16_t attr,struct ble_gatt_access_ctxt *ctx,void *arg){
    struct ble_gap_conn_desc desc;
    if(ble_gap_conn_find(conn,&desc)||!desc.sec_state.encrypted||!desc.sec_state.authenticated)return BLE_ATT_ERR_INSUFFICIENT_AUTHEN;
    if(ctx->op==BLE_GATT_ACCESS_OP_READ_CHR){char json[512];size_t n=network_config_read(json,sizeof(json));return n&&os_mbuf_append(ctx->om,json,n)==0?0:BLE_ATT_ERR_INSUFFICIENT_RES;}
    if(ctx->op!=BLE_GATT_ACCESS_OP_WRITE_CHR)return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    uint8_t packet[20];if(OS_MBUF_PKTLEN(ctx->om)!=20||ble_hs_mbuf_to_flat(ctx->om,packet,20,NULL))return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    return network_config_write(packet);
}

static const struct ble_gatt_svc_def services[] = {
    {.type = BLE_GATT_SVC_TYPE_PRIMARY, .uuid = &service_uuid.u,
     .characteristics = (struct ble_gatt_chr_def[]) {
         {.uuid = &write_uuid.u, .access_cb = access_cb, .flags = BLE_GATT_CHR_F_WRITE},
         {.uuid = &notify_uuid.u, .access_cb = access_cb,
          .flags = BLE_GATT_CHR_F_NOTIFY, .val_handle = &notify_handle},
         {.uuid=&network_uuid.u,.access_cb=network_access,.flags=BLE_GATT_CHR_F_READ|BLE_GATT_CHR_F_WRITE|BLE_GATT_CHR_F_READ_AUTHEN|BLE_GATT_CHR_F_WRITE_AUTHEN},
         {0}
     }},
    {0}
};

static int gap_event(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            if(!network_wifi_mode()&&!network_usb_mode())reset_home();
            atomic_store(&connection, event->connect.conn_handle);
            ESP_LOGI(TAG, "Phone connected");
        } else advertise();
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        atomic_store(&subscribed, false);
        if(!network_wifi_mode()&&!network_usb_mode())ble_bridge_reset_session();
        network_config_disconnect();
        atomic_store(&connection, BLE_HS_CONN_HANDLE_NONE);
        ESP_LOGI(TAG, "Phone disconnected; advertising again");
        advertise();
        break;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == notify_handle) {
            atomic_store(&subscribed, event->subscribe.cur_notify != 0);
            if(event->subscribe.cur_notify){bridge_packet_t features={.bytes={1,15,1,0}};ble_bridge_publish(&features);bridge_packet_t name=name_status(0);ble_bridge_publish(&name);}
            ESP_LOGI(TAG, "CAN notifications %s", event->subscribe.cur_notify ? "enabled" : "disabled");
        }
        break;
    case BLE_GAP_EVENT_PASSKEY_ACTION: {
        if(event->passkey.params.action!=BLE_SM_IOACT_DISP){ble_gap_terminate(event->passkey.conn_handle,BLE_ERR_AUTH_FAIL);break;}
        struct ble_sm_io io={.action=BLE_SM_IOACT_DISP,.passkey=esp_random()%1000000};
        network_pairing_code(io.passkey);if(ble_sm_inject_io(event->passkey.conn_handle,&io))network_pairing_end();break;
    }
    case BLE_GAP_EVENT_ENC_CHANGE: network_pairing_end();break;
    case BLE_GAP_EVENT_REPEAT_PAIRING: {
        struct ble_gap_conn_desc desc;
        if(!ble_gap_conn_find(event->repeat_pairing.conn_handle,&desc))ble_store_util_delete_peer(&desc.peer_id_addr);
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    }
    case BLE_GAP_EVENT_ADV_COMPLETE:
        advertise();
        break;
    default: break;
    }
    return 0;
}

static void advertise(void)
{
    if(network_dual_mode()||network_usb_mode())return;
    struct ble_hs_adv_fields fields = {
        .flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP,
        .uuids128 = (ble_uuid128_t *)&service_uuid,
        .num_uuids128 = 1, .uuids128_is_complete = 1,
    };
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc) { ESP_LOGE(TAG, "Advertising fields: %d", rc); return; }
    const char *name = ble_svc_gap_device_name();
    struct ble_hs_adv_fields response = {
        .name = (uint8_t *)name, .name_len = strlen(name), .name_is_complete = 1,
    };
    rc = ble_gap_adv_rsp_set_fields(&response);
    if (rc) { ESP_LOGE(TAG, "Scan response: %d", rc); return; }
    struct ble_gap_adv_params params = {
        .conn_mode = BLE_GAP_CONN_MODE_UND, .disc_mode = BLE_GAP_DISC_MODE_GEN,
    };
    rc = ble_gap_adv_start(address_type, NULL, BLE_HS_FOREVER, &params, gap_event, NULL);
    if (rc) ESP_LOGE(TAG, "Advertising start: %d", rc);
    else ESP_LOGI(TAG, "Advertising as %s", name);
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (!rc) rc = ble_hs_id_infer_auto(0, &address_type);
    if (rc) { ESP_LOGE(TAG, "BLE address: %d", rc); return; }
    advertise();
}
static void on_reset(int reason)
{
    if(!network_wifi_mode()&&!network_usb_mode())ble_bridge_reset_session();
    network_config_disconnect();
    atomic_store(&subscribed, false);
    atomic_store(&connection, BLE_HS_CONN_HANDLE_NONE);
    ESP_LOGW(TAG, "BLE reset: %d", reason);
}
static void host_task(void *arg)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}
static bool notify_connected(void *context)
{
    return ble_bridge_connected();
}
static void *notify_allocate(void *context, const bridge_packet_t *packet)
{
    return ble_hs_mbuf_from_flat(packet->bytes, sizeof(packet->bytes));
}
static unsigned notify_pool_free(void *context, void *buffer)
{
    struct os_mbuf *om = buffer;
    // os_msys_num_free sums both pools. Small ATT packets cannot fall back to
    // the large pool, so inspect this allocation's pool with NimBLE's lock.
    os_sr_t critical;
    OS_ENTER_CRITICAL(critical);
    unsigned free_blocks = om->om_omp->omp_pool->mp_num_free;
    OS_EXIT_CRITICAL(critical);
    return free_blocks;
}
static void notify_release(void *context, void *buffer)
{
    os_mbuf_free_chain(buffer);
}
static bool notify_submit(void *context, void *buffer)
{
    // notify_custom consumes its mbuf even when it returns an error.
    return ble_gatts_notify_custom(atomic_load(&connection), notify_handle, buffer) == 0;
}
static bool notify_send(void *context, const bridge_packet_t *packet)
{
    if(network_usb_mode())return usb_link_send(packet);
    if(network_wifi_mode())return network_send(packet);
    static const bridge_notify_resource_ops_t ops = {
        .allocate = notify_allocate, .pool_free = notify_pool_free,
        .release = notify_release, .submit = notify_submit,
    };
    return bridge_notify_with_reserve(packet, &ops, context);
}
static void notify_delay(void *context, unsigned milliseconds)
{
    vTaskDelay(pdMS_TO_TICKS(milliseconds));
}
static void notify_task(void *arg)
{
    bridge_packet_t packet;
    TickType_t last_name = 0,last_home=0,last_network=0;
    bool dual_paused=false;
    for (;;) {
        TickType_t now = xTaskGetTickCount();
        if(network_dual_mode()||network_usb_mode()){
            if(!dual_paused&&ble_hs_synced()){
                ble_gap_adv_stop();dual_paused=true;
            }
            int conn=atomic_load(&connection);
            if(conn!=BLE_HS_CONN_HANDLE_NONE)ble_gap_terminate(conn,BLE_ERR_REM_USER_CONN_TERM);
            if(network_dual_mode()){vTaskDelay(pdMS_TO_TICKS(20));continue;}
        }
        if(!network_usb_mode()&&dual_paused&&ble_hs_synced()){dual_paused=false;advertise();}
        if(atomic_load(&subscribed)&&now-last_network>=pdMS_TO_TICKS(1000)){
            bridge_packet_t mode={.bytes={1,17,network_wifi_mode(),0}};
            struct os_mbuf *om=ble_hs_mbuf_from_flat(mode.bytes,20);
            if(om)ble_gatts_notify_custom(atomic_load(&connection),notify_handle,om);
            last_network=now;
        }
        if (ble_bridge_connected() && now - last_name >= pdMS_TO_TICKS(1000)) {
            bridge_packet_t status = name_status(0);
            ble_bridge_publish(&status);
            last_name = now;
        }
        if(ble_bridge_connected()&&now-last_home>=pdMS_TO_TICKS(1000)){
            bridge_packet_t summary;bool quiet;
            portENTER_CRITICAL(&home_lock);quiet=home_status.mode==1;home_snapshot(&home_status,&summary,esp_timer_get_time()/1000);portEXIT_CRITICAL(&home_lock);
            if(quiet)ble_bridge_publish(&summary);
            for(unsigned start=0;start<HOME_MOTORS;start+=HOME_TEMPERATURES_PER_PACKET){
                bridge_packet_t temperatures;portENTER_CRITICAL(&home_lock);bool have_temperatures=home_temperature_snapshot(&home_status,&temperatures,start,esp_timer_get_time()/1000);portEXIT_CRITICAL(&home_lock);
                if(!have_temperatures)break;
                ble_bridge_publish(&temperatures);
            }
            bridge_packet_t rates;portENTER_CRITICAL(&home_lock);bool report=home_detail_stats(&home_status,&rates,esp_timer_get_time()/1000);portEXIT_CRITICAL(&home_lock);
            if(report)ble_bridge_publish(&rates);
            last_home=now;
        }
        bridge_packet_t sample;portENTER_CRITICAL(&home_lock);bool have_sample=!home_status.sample_hz&&home_detail(&home_status,&sample,esp_timer_get_time()/1000);portEXIT_CRITICAL(&home_lock);
        if(have_sample)ble_bridge_publish(&sample);
        bool ack = xQueueReceive(ack_notify_queue, &packet, 0) == pdTRUE;
        bool control = !ack && xQueueReceive(control_notify_queue, &packet, 0) == pdTRUE;
        if (!ack && !control && xQueueReceive(notify_queue, &packet, pdMS_TO_TICKS(10)) != pdTRUE) continue;
        if (!ble_bridge_connected()) continue;
        if(suppress_packet(&packet))continue;
        if(ack) {
            if(!bridge_deliver_ack(&packet,notify_connected,notify_send,notify_delay,NULL))
                atomic_fetch_add(&dropped,1);
            continue;
        }
        bool delivered = false;
        for (unsigned attempt = 0; attempt < (control ? 3 : 1); attempt++) {
            if (!ble_bridge_connected()) break;
            if (notify_send(NULL, &packet)) {
                delivered = true;
                break;
            }
            if (control) vTaskDelay(pdMS_TO_TICKS(10));
        }
        if (!delivered) {
            atomic_fetch_add(&dropped, 1);
            // A full raw queue must not busy-loop allocating and freeing the
            // same exhausted pool. Yield only on failure; healthy traffic has
            // no added rate limit, and the next loop checks the ACK queue first.
            if (!control) vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}
void ble_bridge_publish(const bridge_packet_t *packet)
{
    portENTER_CRITICAL(&home_lock);
    bridge_packet_t sample;bool have_sample=false;
    if(packet->bytes[1]==1)home_observe(&home_status,packet,esp_timer_get_time()/1000);
    if(packet->bytes[1]==1&&home_status.sample_hz)have_sample=home_detail(&home_status,&sample,esp_timer_get_time()/1000);
    bool suppress=home_redundant(packet);
    portEXIT_CRITICAL(&home_lock);
    if(have_sample)ble_bridge_publish(&sample);
    if(suppress)return; // intentional suppression, not a dropped frame
    QueueHandle_t queue = bridge_packet_is_ack(packet) ? ack_notify_queue :
        (bridge_packet_is_raw(packet) ? notify_queue : control_notify_queue);
    if (ble_bridge_connected() && xQueueSend(queue, packet, 0) != pdTRUE)
        atomic_fetch_add(&dropped, 1);
}
uint32_t ble_bridge_dropped(void) { return atomic_load(&dropped); }
bool ble_bridge_connected(void) { if(network_dual_mode())return false;return network_usb_mode()?usb_link_connected():network_wifi_mode()?network_connected():atomic_load(&subscribed); }
bool ble_bridge_take_stop(void) { return atomic_exchange(&stop_requested, false); }
void ble_bridge_set_motor_busy(bool busy) { atomic_store(&motor_busy, busy); }

void ble_bridge_init(QueueHandle_t commands, QueueHandle_t queries)
{
    command_queue = commands;
    query_command_queue = queries;
    ack_notify_queue = xQueueCreate(16, sizeof(bridge_packet_t));
    notify_queue = xQueueCreate(256, sizeof(bridge_packet_t));
    control_notify_queue = xQueueCreate(16, sizeof(bridge_packet_t));
    ESP_ERROR_CHECK(ack_notify_queue && notify_queue && control_notify_queue ? ESP_OK : ESP_ERR_NO_MEM);
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    bridge_name_load(saved_name);
    ESP_ERROR_CHECK(nimble_port_init());
    ble_hs_cfg.sm_io_cap=BLE_HS_IO_DISPLAY_ONLY;
    ble_hs_cfg.sm_bonding=1;ble_hs_cfg.sm_mitm=1;ble_hs_cfg.sm_sc=1;
    ble_hs_cfg.sm_our_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.store_status_cb=ble_store_util_status_rr;ble_store_config_init();
    usb_link_init();
    network_init();
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_ERROR_CHECK(ble_gatts_count_cfg(services));
    ESP_ERROR_CHECK(ble_gatts_add_svcs(services));
    ESP_ERROR_CHECK(ble_svc_gap_device_name_set(saved_name));
    ESP_ERROR_CHECK(xTaskCreate(notify_task, "ble_notify", 4096, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    nimble_port_freertos_init(host_task);
}

bool ble_bridge_uart_publish(const bridge_packet_t *packet) {
    if(!uart_bridge_selected() || !ble_bridge_connected() || !notify_queue)return false;
    if(xQueueSend(notify_queue,packet,0)==pdTRUE)return true;
    atomic_fetch_add(&dropped,1);return false;
}
