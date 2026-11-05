#include "ble_bridge.h"
#include "network_link.h"
#include "usb_link.h"
#include "uart_bridge.h"
#include "wifi_uart_diag.h"
#include "device_settings.h"
#include "esp_random.h"
void ble_store_config_init(void);
#include "home_status.h"
#include "ack_delivery.h"
#include "notify_resource.h"
#include "notify_schedule.h"
#include "ble_connection_policy.h"
#include "sdkconfig.h"
#if CONFIG_BLE_EVENT_SCHEDULER
#define BLE_EVENT_SCHEDULER_ENABLED 1
#else
#define BLE_EVENT_SCHEDULER_ENABLED 0
#endif
#include "ble_diagnostics.h"
#include "esp_heap_caps.h"
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
static const ble_uuid128_t batch_notify_uuid = BLE_UUID128_INIT(UUID_BYTES(6));
static const ble_uuid128_t network_uuid = BLE_UUID128_INIT(UUID_BYTES(4));
#if CONFIG_BLE_TEST_DIAGNOSTICS
static const ble_uuid128_t diagnostics_uuid = BLE_UUID128_INIT(UUID_BYTES(5));
#endif
static const char *TAG = "can_ble";
static QueueHandle_t command_queue, query_command_queue, ack_notify_queue, notify_queue, control_notify_queue;
static uint16_t notify_handle,batch_notify_handle;
static atomic_bool batch_subscribed,legacy_subscribed;
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
static atomic_bool wifi_paused, wifi_pause_ready;
static struct ble_npl_event wifi_radio_event;
static atomic_uint dropped;
static atomic_bool motor_busy, stop_requested;
static void advertise(void);
typedef struct {
    bridge_packet_t packet;
    uint32_t generation;
#if CONFIG_BLE_TEST_DIAGNOSTICS
    int64_t enqueued_us;
#endif
} notify_item_t;
static atomic_uint notify_generation,ble_generation;
static TaskHandle_t notify_worker;
static void wake_notify(void){if(notify_worker)xTaskNotifyGive(notify_worker);}
static uint32_t packet_generation(const bridge_packet_t *p){
    return atomic_load(p->bytes[1]==17?&ble_generation:&notify_generation);
}
static unsigned packet_class(const bridge_packet_t *p){
    return bridge_packet_is_ack(p)?BD_ACK:bridge_packet_is_raw(p)?BD_RAW:BD_CONTROL;
}
#if CONFIG_BLE_TEST_DIAGNOSTICS
static unsigned packet_bytes(const bridge_packet_t *p){return p->bytes[1]==19?p->bytes[2]:0;}
#endif
static void wifi_note_queue(QueueHandle_t queue){
    if(!network_wifi_mode())return;
    unsigned depth=queue==notify_queue?WD_NOTIFY_RAW_DEPTH:
        queue==control_notify_queue?WD_NOTIFY_CONTROL_DEPTH:WD_NOTIFY_ACK_DEPTH;
    wifi_diag_queue(depth,depth+1,uxQueueMessagesWaiting(queue));
}
static bool enqueue_packet(QueueHandle_t queue,const bridge_packet_t *p){
    notify_item_t item={.packet=*p,.generation=packet_generation(p)};
#if CONFIG_BLE_TEST_DIAGNOSTICS
    item.enqueued_us=esp_timer_get_time();
    unsigned cls=packet_class(p);
#endif
    bool ok=xQueueSend(queue,&item,0)==pdTRUE;
    wifi_note_queue(queue);
    if(!ok)wifi_diag_drop(WD_DROP_NOTIFY_FULL,p);
    if(ok){bd_add(bd_index(cls,BD_ENQUEUED),1);bd_max(bd_index(cls,BD_HIGH_WATER),uxQueueMessagesWaiting(queue));}
    else{bd_add(bd_index(cls,BD_FULL_PACKETS),1);bd_add(bd_index(cls,BD_FULL_BYTES),packet_bytes(p));}
    if(ok)wake_notify();
    return ok;
}
static bool dequeue_item(QueueHandle_t queue,notify_item_t *item,TickType_t wait){
    if(xQueueReceive(queue,item,wait)!=pdTRUE)return false;
    wifi_note_queue(queue);
    bd_max(bd_index(packet_class(&item->packet),BD_MAX_AGE_MS),(esp_timer_get_time()-item->enqueued_us)/1000);
    return true;
}
static bool dequeue_packet(QueueHandle_t queue,bridge_packet_t *p,TickType_t wait){
    notify_item_t item;
    if(!dequeue_item(queue,&item,wait))return false;
    *p=item.packet;
    return true;
}
static void discard_raw_queue(void){
    if(!notify_queue)return;
    bridge_packet_t packet;
    while(dequeue_packet(notify_queue,&packet,0)){
        bd_add(bd_index(BD_RAW,BD_RESET_BYTES),packet_bytes(&packet));
        wifi_diag_drop(WD_DROP_NOTIFY_RESET,&packet);
        uart_bridge_drop_packet(&packet);
    }
}
#if CONFIG_BLE_TEST_DIAGNOSTICS
static void observe_connection(uint16_t conn){
    struct ble_gap_conn_desc d;
    if(!ble_gap_conn_find(conn,&d)){
        bd_set(BD_INTERVAL,d.conn_itvl);bd_set(BD_LATENCY,d.conn_latency);
        bd_set(BD_TIMEOUT,d.supervision_timeout);bd_set(BD_MTU,ble_att_mtu(conn));
        uint8_t tx=0,rx=0;
        if(!ble_gap_read_le_phy(conn,&tx,&rx)){bd_set(BD_PHY_TX,tx);bd_set(BD_PHY_RX,rx);}
    }
}
static int diagnostics_access(uint16_t conn,uint16_t attr,struct ble_gatt_access_ctxt *ctx,void *arg){
    if(ctx->op!=BLE_GATT_ACCESS_OP_READ_CHR)return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    uint32_t snapshot[BD_TOTAL_WORDS];
    bd_set(BD_VERSION,2);bd_set(BD_WORDS,BD_TOTAL_WORDS);
    bd_set(BD_UPTIME_MS,esp_timer_get_time()/1000);
    bd_set(BD_HEAP_FREE,esp_get_free_heap_size());bd_set(BD_HEAP_MIN,esp_get_minimum_free_heap_size());
    bd_set(BD_POOL1_COUNT,CONFIG_BT_NIMBLE_MSYS_1_BLOCK_COUNT);
    bd_set(BD_POOL2_COUNT,CONFIG_BT_NIMBLE_MSYS_2_BLOCK_COUNT);
    QueueHandle_t queues[]={notify_queue,control_notify_queue,ack_notify_queue};
    for(unsigned c=0;c<BD_CLASSES;c++){
        bd_set(bd_index(c,BD_DEPTH),uxQueueMessagesWaiting(queues[c]));
        notify_item_t head;
        if(xQueuePeek(queues[c],&head,0)==pdTRUE)
            bd_max(bd_index(c,BD_MAX_AGE_MS),(esp_timer_get_time()-head.enqueued_us)/1000);
    }
    for(unsigned i=0;i<BD_TOTAL_WORDS;i++)snapshot[i]=atomic_load(&bd[i]);
    uart_bridge_diagnostics(snapshot+BD_UART_START);
    return os_mbuf_append(ctx->om,snapshot,sizeof(snapshot))?BLE_ATT_ERR_INSUFFICIENT_RES:0;
}
#endif
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
        if(p[1]==0x17){discard_raw_queue();ble_bridge_publish(&ack);}
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
    atomic_fetch_add(&notify_generation,1);wake_notify();
    reset_home();uart_bridge_disconnect();atomic_store(&stop_requested,true);
    if(notify_queue)discard_raw_queue();
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
         {.uuid=&batch_notify_uuid.u,.access_cb=access_cb,.flags=BLE_GATT_CHR_F_NOTIFY,.val_handle=&batch_notify_handle},
#if CONFIG_BLE_TEST_DIAGNOSTICS
         {.uuid=&diagnostics_uuid.u,.access_cb=diagnostics_access,.flags=BLE_GATT_CHR_F_READ},
#endif
         {0}
     }},
    {0}
};

#if CONFIG_BLE_CONNECTION_POLICY
static struct ble_npl_callout parameter_timer;
static bridge_ble_policy_t parameter_policy;
static void parameter_tick(struct ble_npl_event *event){
    (void)event;
    uint32_t generation=atomic_load(&ble_generation);
    int conn=atomic_load(&connection);
    struct ble_gap_conn_desc desc;
    bool ready=atomic_load(&subscribed)&&!network_wifi_mode()&&!network_usb_mode()&&!network_dual_mode();
    if(!ready||conn==BLE_HS_CONN_HANDLE_NONE||ble_gap_conn_find(conn,&desc))return;
    int64_t now=esp_timer_get_time()/1000;
    bridge_ble_params_t target;
    if(bridge_ble_policy_next(&parameter_policy,generation,now,CONFIG_BLE_CONNECTION_POLICY,
                               ready,desc.conn_itvl,desc.conn_latency,desc.supervision_timeout,&target)){
        struct ble_gap_upd_params update={.itvl_min=target.min_interval,.itvl_max=target.max_interval,
            .latency=target.latency,.supervision_timeout=target.timeout};
        // Runs on the NimBLE host queue, serialized with connect/disconnect.
        int rc=ble_gap_update_params(conn,&update);
        bd_add(BD_PARAM_REQUESTS,1);bd_set(BD_PARAM_REQUEST_RC,rc);
        bridge_ble_policy_result(&parameter_policy,rc);
    }
    if(!parameter_policy.finished&&parameter_policy.attempts<2){
        int64_t remaining=parameter_policy.due_ms-esp_timer_get_time()/1000;
        ble_npl_callout_reset(&parameter_timer,ble_npl_time_ms_to_ticks32(remaining>0?remaining:1));
    }
}
static void parameter_subscribe(bool enabled){
    if(!enabled){ble_npl_callout_stop(&parameter_timer);return;}
    uint32_t generation=atomic_load(&ble_generation);
    if(parameter_policy.generation==generation)return; // CCCD toggles cannot renew the request budget.
    bridge_ble_policy_start(&parameter_policy,generation,esp_timer_get_time()/1000);
    ble_npl_callout_reset(&parameter_timer,ble_npl_time_ms_to_ticks32(1000));
}
#endif

static int gap_event(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            if(!network_wifi_mode()&&!network_usb_mode())reset_home();
            atomic_store(&connection, event->connect.conn_handle);
            atomic_fetch_add(&ble_generation,1);wake_notify();
            bd_add(BD_GENERATION,1);bd_add(BD_CONNECTS,1);
#if CONFIG_BLE_TEST_DIAGNOSTICS
            observe_connection(event->connect.conn_handle);
#endif
            ESP_LOGI(TAG, "Phone connected");
        } else advertise();
        break;
    case BLE_GAP_EVENT_DISCONNECT:
#if CONFIG_BLE_CONNECTION_POLICY
        ble_npl_callout_stop(&parameter_timer);
#endif
        atomic_fetch_add(&ble_generation,1);wake_notify();
        bd_add(BD_DISCONNECTS,1);bd_set(BD_DISCONNECT_REASON,event->disconnect.reason);
        atomic_store(&subscribed, false);
        atomic_store(&batch_subscribed, false);atomic_store(&legacy_subscribed, false);
        if(!network_wifi_mode()&&!network_usb_mode())ble_bridge_reset_session();
        network_config_disconnect();
        atomic_store(&connection, BLE_HS_CONN_HANDLE_NONE);
        ESP_LOGI(TAG, "Phone disconnected; advertising again");
        advertise();
        break;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == notify_handle || event->subscribe.attr_handle == batch_notify_handle) {
            atomic_store(event->subscribe.attr_handle==batch_notify_handle?&batch_subscribed:&legacy_subscribed,event->subscribe.cur_notify!=0);
            atomic_store(&subscribed,atomic_load(&batch_subscribed)||atomic_load(&legacy_subscribed));wake_notify();
#if CONFIG_BLE_CONNECTION_POLICY
            parameter_subscribe(atomic_load(&subscribed));
#endif
            if(event->subscribe.cur_notify){bridge_packet_t features={.bytes={1,15,1,0}};ble_bridge_publish(&features);bridge_packet_t name=name_status(0);ble_bridge_publish(&name);}
            ESP_LOGI(TAG, "CAN notifications %s", event->subscribe.cur_notify ? "enabled" : "disabled");
        }
        break;
    case BLE_GAP_EVENT_CONN_UPDATE:
        bd_add(BD_PARAM_CHANGES,1);bd_set(BD_PARAM_STATUS,event->conn_update.status);
#if CONFIG_BLE_TEST_DIAGNOSTICS
        observe_connection(event->conn_update.conn_handle);
#endif
        break;
    case BLE_GAP_EVENT_PHY_UPDATE_COMPLETE:
        if(!event->phy_updated.status&&event->phy_updated.conn_handle==atomic_load(&connection)){
            bd_set(BD_PHY_TX,event->phy_updated.tx_phy);bd_set(BD_PHY_RX,event->phy_updated.rx_phy);
        }
        break;
    case BLE_GAP_EVENT_MTU:
        bd_set(BD_MTU,event->mtu.value);
        break;
    case BLE_GAP_EVENT_NOTIFY_TX:
        if(event->notify_tx.conn_handle==atomic_load(&connection)&&(event->notify_tx.attr_handle==notify_handle||event->notify_tx.attr_handle==batch_notify_handle)){
            wake_notify(); // Local attempt hint only; never a remote ACK/credit.
            bd_add(BD_NOTIFY_EVENTS,1);
            if(event->notify_tx.status)bd_add(BD_NOTIFY_ERRORS,1);
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
    if(atomic_load(&wifi_paused)||network_wifi_mode()||network_dual_mode()||network_usb_mode())return;
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
#if CONFIG_BLE_CONNECTION_POLICY
    ble_npl_callout_stop(&parameter_timer);
#endif
    atomic_fetch_add(&ble_generation,1);wake_notify();
    if(!network_wifi_mode()&&!network_usb_mode())ble_bridge_reset_session();
    network_config_disconnect();
    atomic_store(&subscribed, false);
    atomic_store(&batch_subscribed, false);atomic_store(&legacy_subscribed, false);
    atomic_store(&connection, BLE_HS_CONN_HANDLE_NONE);
    ESP_LOGW(TAG, "BLE reset: %d", reason);
}
static void host_task(void *arg)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}
static bool packet_connected(const bridge_packet_t *packet){
    return packet->bytes[1]==17?atomic_load(&subscribed):ble_bridge_connected();
}
static bool notify_connected(void *context)
{
    return (!context||*(uint32_t *)context==atomic_load(&notify_generation))&&ble_bridge_connected();
}
static unsigned notify_failure;
typedef struct {const bridge_packet_t *packet;const uint8_t *bytes;unsigned size;} notify_payload_t;
static void *notify_allocate(void *context, const bridge_packet_t *packet)
{
    notify_payload_t *payload=context;
    void *buffer=ble_hs_mbuf_from_flat(payload->bytes,payload->size);
    if(!buffer)notify_failure=BD_DROP_ALLOC_BYTES;
    if(!buffer)bd_add(bd_index(packet_class(packet),BD_ALLOC_FAIL),1);
    return buffer;
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
    bd_min(BD_POOL_MIN,free_blocks);
    if(free_blocks<BRIDGE_NOTIFY_ATT_RESERVE+BRIDGE_NOTIFY_HEADER_BLOCKS)notify_failure=BD_DROP_RESERVE_BYTES;
    if(free_blocks<BRIDGE_NOTIFY_ATT_RESERVE+BRIDGE_NOTIFY_HEADER_BLOCKS)
        bd_add(bd_index(packet_class(((notify_payload_t *)context)->packet),BD_RESERVE_REJECT),1);
    return free_blocks;
}
static void notify_release(void *context, void *buffer)
{
    os_mbuf_free_chain(buffer);
}
static bool notify_submit(void *context, void *buffer)
{
    // notify_custom consumes its mbuf even when it returns an error.
    int rc=ble_gatts_notify_custom(atomic_load(&connection),atomic_load(&batch_subscribed)?batch_notify_handle:notify_handle,buffer);
    if(rc){
        notify_failure=BD_DROP_SUBMIT_BYTES;
        bd_set(BD_LAST_ERROR_RC,rc);
        bd_add(rc==BLE_HS_ENOMEM?BD_RC_ENOMEM:rc==BLE_HS_ENOTCONN?BD_RC_ENOTCONN:rc==BLE_HS_EAGAIN?BD_RC_EAGAIN:BD_RC_OTHER,1);
    }
    bd_set(BD_NOTIFY_RC,rc);
    bd_add(bd_index(packet_class(((notify_payload_t *)context)->packet),rc?BD_SUBMIT_ERROR:BD_SUBMITTED),1);
    return rc==0;
}
static bool notify_send(void *context, const bridge_packet_t *packet)
{
    notify_failure=BD_OTHER_BYTES;
    if(context&&*(uint32_t *)context!=packet_generation(packet))return false;
    // type17 describes the BLE provisioning link even while LAN is primary.
    if(packet->bytes[1]!=17){
        if(network_usb_mode())return usb_link_send(packet);
        if(network_wifi_mode())return network_send(packet);
    }
    static const bridge_notify_resource_ops_t ops = {
        .allocate = notify_allocate, .pool_free = notify_pool_free,
        .release = notify_release, .submit = notify_submit,
    };
    notify_payload_t payload={packet,packet->bytes,sizeof(packet->bytes)};
    return bridge_notify_with_reserve(packet, &ops, &payload);
}
#if CONFIG_BLE_EVENT_SCHEDULER
static bool ble_resource_ready(void){
    // Probe the same selected payload pool as notify_send; never sum pools.
    uint8_t bytes[20]={0};
    struct os_mbuf *om=ble_hs_mbuf_from_flat(bytes,sizeof(bytes));
    if(!om){bd_min(BD_POOL_MIN,0);return false;}
    os_sr_t critical;OS_ENTER_CRITICAL(critical);
    unsigned free_blocks=om->om_omp->omp_pool->mp_num_free;
    OS_EXIT_CRITICAL(critical);
    bd_min(BD_POOL_MIN,free_blocks);
    os_mbuf_free_chain(om);
    return free_blocks>=BRIDGE_NOTIFY_ATT_RESERVE+BRIDGE_NOTIFY_HEADER_BLOCKS;
}

#endif
static void notify_delay(void *context, unsigned milliseconds)
{
#if CONFIG_BLE_EVENT_SCHEDULER
    if(network_usb_mode()||network_wifi_mode()){
        vTaskDelay(pdMS_TO_TICKS(milliseconds));return; // Existing non-BLE retry policy.
    }
    int64_t deadline=esp_timer_get_time()+(int64_t)milliseconds*1000;
    while(notify_connected(context)){
        int64_t remaining=deadline-esp_timer_get_time();
        if(remaining<=0)break;
        TickType_t ticks=pdMS_TO_TICKS((remaining+999)/1000);
        ulTaskNotifyTake(pdTRUE,ticks?ticks:1);
        if(ble_resource_ready())break;
    }
#else
    vTaskDelay(pdMS_TO_TICKS(milliseconds));
#endif
}
// Optional characteristic c0de0006 carries concatenated, unchanged v1 packets.
// The CAN lane gathers complete packets, always checking ACK/control queues
// before ordinary telemetry. UART and legacy subscribers retain their paths.
static void deliver_can_batch(const notify_item_t *first)
{
    if(first->generation!=packet_generation(&first->packet)||!packet_connected(&first->packet)||suppress_packet(&first->packet))return;
    uint32_t generation=atomic_load(&notify_generation),ble_epoch=atomic_load(&ble_generation);
    unsigned mtu=ble_att_mtu(atomic_load(&connection));
    unsigned limit=mtu>3?(mtu-3)/20:1;
    // 200 bytes fit one default 256-byte msys block after NimBLE headers.
    // A 240-byte value chains a second block and halves useful pool capacity.
    if(limit>10)limit=10;
    if(!limit)limit=1;
    uint8_t bytes[240];unsigned count=0,classes[BD_CLASSES]={0},examined=0;
    int64_t deadline=esp_timer_get_time()+10000;
    notify_item_t item=*first;
    do {
        examined++;
        if(item.generation==packet_generation(&item.packet)&&packet_connected(&item.packet)&&!suppress_packet(&item.packet)){
            memcpy(bytes+20*count,item.packet.bytes,20);count++;classes[packet_class(&item.packet)]++;
        }else uart_bridge_drop_packet(&item.packet);
        if(count>=limit||examined>=24)break;
        if(dequeue_item(ack_notify_queue,&item,0)||dequeue_item(control_notify_queue,&item,0))continue;
        // Sparse management replies never wait for raw traffic. CAN traffic
        // gathers for 10ms, with at most one additional RTOS tick at the edge.
        TickType_t wait=!classes[BD_ACK]&&!classes[BD_CONTROL]&&esp_timer_get_time()<deadline?1:0;
        if(!dequeue_item(notify_queue,&item,wait))break;
    }while(count<limit);
    if(!count)return;
    static const bridge_notify_resource_ops_t ops={
        .allocate=notify_allocate,.pool_free=notify_pool_free,.release=notify_release,.submit=notify_submit,
    };
    notify_payload_t payload={&first->packet,bytes,count*20};
    bool delivered=false;
    unsigned attempts=8;
    for(unsigned attempt=0;attempt<attempts;attempt++){
        if(generation!=atomic_load(&notify_generation)||ble_epoch!=atomic_load(&ble_generation)||!packet_connected(&first->packet)||!atomic_load(&batch_subscribed))return;
        if(bridge_notify_with_reserve(&first->packet,&ops,&payload)){delivered=true;break;}
        if(attempt+1<attempts)notify_delay(&generation,10);
    }
    if(delivered){
        classes[packet_class(&first->packet)]--; // notify_submit already counted its first packet.
        for(unsigned cls=0;cls<BD_CLASSES;cls++)bd_add(bd_index(cls,BD_SUBMITTED),classes[cls]);
    }else{
        for(unsigned cls=0;cls<BD_CLASSES;cls++)bd_add(bd_index(cls,BD_FAILED_PACKETS),classes[cls]);
        atomic_fetch_add(&dropped,count);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
// A false USB/LAN enqueue or failed BLE submission leaves this packet here.
// Their queues account for later failures only after accepting ownership.
static void deliver_packet(const bridge_packet_t *packet,bool ack,bool control,uint32_t generation)
{
    if(generation!=packet_generation(packet)||!packet_connected(packet) || suppress_packet(packet)){
        wifi_diag_drop(generation!=packet_generation(packet)?WD_DROP_GENERATION:
            !packet_connected(packet)?WD_DROP_INACTIVE:WD_DROP_SUPPRESSED,packet);
        bd_add(bd_index(packet_class(packet),BD_OTHER_BYTES),packet_bytes(packet));
        uart_bridge_drop_packet(packet);
        return;
    }
    if(ack){
        if(!bridge_deliver_ack(packet,notify_connected,notify_send,notify_delay,&generation)){
            bd_add(bd_index(BD_ACK,BD_FAILED_PACKETS),1);
            atomic_fetch_add(&dropped,1);
        }
        return;
    }
    bool delivered=false;
    bool retry_control=control&&packet->bytes[1]!=17; // Periodic type17 stays best effort.
    for(unsigned attempt=0;attempt<(retry_control?3:1);attempt++){
        if(generation!=packet_generation(packet)||!packet_connected(packet))break;
        if(notify_send(&generation,packet)){delivered=true;break;}
        if(retry_control)notify_delay(&generation,10);
    }
    if(!delivered){
        wifi_diag_drop(WD_DROP_ROUTE,packet);
        bd_add(bd_index(packet_class(packet),notify_failure),packet_bytes(packet));
        bd_add(bd_index(packet_class(packet),BD_FAILED_PACKETS),1);
        atomic_fetch_add(&dropped,1);
        uart_bridge_drop_packet(packet);
        // Experimental BLE failures skip the old mandatory failure sleep.
        if(!control&&(!BLE_EVENT_SCHEDULER_ENABLED||network_usb_mode()||network_wifi_mode()))vTaskDelay(pdMS_TO_TICKS(10));
    }
}
#if CONFIG_BLE_EVENT_SCHEDULER
static int64_t schedule_now(void *context){return esp_timer_get_time();}
static bool schedule_pending(void *context){return uxQueueMessagesWaiting(notify_queue)!=0;}
static bool schedule_ready(void *context){
    if(network_usb_mode()||network_wifi_mode())return true;
    bool ready=ble_resource_ready();
    if(!ready)bd_add(BD_RESOURCE_WAITS,1);
    return ready;
}
static bool schedule_take(void *context,unsigned lane,bridge_notify_work_t *work){
    QueueHandle_t queues[]={notify_queue,control_notify_queue,ack_notify_queue};
    notify_item_t item;
    if(!dequeue_item(queues[lane],&item,0))return false;
    work->packet=item.packet;work->generation=item.generation;
    return true; // deliver rechecks generation, including across ACK retries.
}
static void schedule_deliver(void *context,const bridge_notify_work_t *work,unsigned lane){
    deliver_packet(&work->packet,lane==BRIDGE_NOTIFY_ACK,lane==BRIDGE_NOTIFY_CONTROL,work->generation);
}
static void schedule_wait(void *context,unsigned milliseconds){
    TickType_t ticks=pdMS_TO_TICKS(milliseconds);
    ulTaskNotifyTake(pdTRUE,ticks?ticks:1);
}
static void schedule_rest(void *context){vTaskDelay(1);}
static const bridge_notify_schedule_ops_t schedule_ops={
    .now_us=schedule_now,.pending_raw=schedule_pending,.raw_ready=schedule_ready,
    .take=schedule_take,.deliver=schedule_deliver,.wait_event=schedule_wait,.rest=schedule_rest,
};
#endif
static void notify_task(void *arg)
{
#if CONFIG_BLE_EVENT_SCHEDULER
    bridge_notify_schedule_t scheduler={0};
#else
    notify_item_t item;
#endif
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
            if(!enqueue_packet(control_notify_queue,&mode))atomic_fetch_add(&dropped,1);
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
#if CONFIG_BLE_EVENT_SCHEDULER
        bridge_notify_schedule_step(&scheduler,&schedule_ops,NULL);
#else
        bool ack=dequeue_item(ack_notify_queue,&item,0);
        bool control=!ack&&dequeue_item(control_notify_queue,&item,0);
        if(!ack&&!control&&!dequeue_item(notify_queue,&item,pdMS_TO_TICKS(10)))continue;
        if(atomic_load(&batch_subscribed)&&!uart_bridge_selected()&&!network_wifi_mode()&&!network_usb_mode())deliver_can_batch(&item);
        else deliver_packet(&item.packet,ack,control,item.generation);
#endif
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
    if (ble_bridge_connected() && !enqueue_packet(queue, packet)){
        atomic_fetch_add(&dropped, 1);
        uart_bridge_drop_packet(packet);
    }
}
uint32_t ble_bridge_dropped(void) { return atomic_load(&dropped); }
uint16_t ble_bridge_connection_interval(void) {
    struct ble_gap_conn_desc desc;
    int handle=atomic_load(&connection);
    return handle!=BLE_HS_CONN_HANDLE_NONE&&!ble_gap_conn_find(handle,&desc)?desc.conn_itvl:0;
}
static void wifi_radio_change(struct ble_npl_event *event) {
    (void)event;
    if(atomic_load(&wifi_paused)) {
#if CONFIG_BLE_CONNECTION_POLICY
        ble_npl_callout_stop(&parameter_timer);
#endif
        if(ble_gap_adv_active()&&ble_gap_adv_stop())return;
        int handle=atomic_load(&connection);
        if(handle!=BLE_HS_CONN_HANDLE_NONE&&ble_gap_terminate(handle,BLE_ERR_REM_USER_CONN_TERM))return;
        atomic_store(&wifi_pause_ready,true);
    } else if(atomic_load(&connection)==BLE_HS_CONN_HANDLE_NONE&&!ble_gap_adv_active())advertise();
}
bool ble_bridge_wifi_pause(void) {
    atomic_store(&wifi_pause_ready,false);atomic_store(&wifi_paused,true);
    ble_npl_eventq_put(nimble_port_get_dflt_eventq(),&wifi_radio_event);
    // The disconnect callback must have run, not just accepted terminate().
    for(unsigned i=0;i<200;i++) {
        if(atomic_load(&wifi_pause_ready)&&atomic_load(&connection)==BLE_HS_CONN_HANDLE_NONE)return true;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return false;
}
void ble_bridge_wifi_resume(void) {
    atomic_store(&wifi_paused,false);
    ble_npl_eventq_put(nimble_port_get_dflt_eventq(),&wifi_radio_event);
}
bool ble_bridge_connected(void) { if(network_dual_mode())return false;return network_usb_mode()?usb_link_connected():network_wifi_mode()?network_connected():atomic_load(&subscribed); }
bool ble_bridge_take_stop(void) { return atomic_exchange(&stop_requested, false); }
void ble_bridge_set_motor_busy(bool busy) { atomic_store(&motor_busy, busy); }

void ble_bridge_init(QueueHandle_t commands, QueueHandle_t queries)
{
    bd_set(BD_POOL_MIN,UINT32_MAX);
    command_queue = commands;
    query_command_queue = queries;
    ack_notify_queue = xQueueCreate(16, sizeof(notify_item_t));
    notify_queue = xQueueCreate(256, sizeof(notify_item_t));
    control_notify_queue = xQueueCreate(16, sizeof(notify_item_t));
    ESP_ERROR_CHECK(ack_notify_queue && notify_queue && control_notify_queue ? ESP_OK : ESP_ERR_NO_MEM);
    esp_err_t err = nvs_flash_init();
    // Preserve pairing/settings on app-only updates; fail instead of erasing NVS.
    ESP_ERROR_CHECK(err);
    bridge_name_load(saved_name);
    ESP_ERROR_CHECK(nimble_port_init());
    ble_npl_event_init(&wifi_radio_event,wifi_radio_change,NULL);
    ble_hs_cfg.sm_io_cap=BLE_HS_IO_DISPLAY_ONLY;
    ble_hs_cfg.sm_bonding=1;ble_hs_cfg.sm_mitm=1;ble_hs_cfg.sm_sc=1;
    ble_hs_cfg.sm_our_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.store_status_cb=ble_store_util_status_rr;ble_store_config_init();
    usb_link_init();
    network_init();
    ble_hs_cfg.sync_cb = on_sync;
#if CONFIG_BLE_CONNECTION_POLICY
    ble_npl_callout_init(&parameter_timer,nimble_port_get_dflt_eventq(),parameter_tick,NULL);
#endif
    ble_hs_cfg.reset_cb = on_reset;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_ERROR_CHECK(ble_gatts_count_cfg(services));
    ESP_ERROR_CHECK(ble_gatts_add_svcs(services));
    ESP_ERROR_CHECK(ble_svc_gap_device_name_set(saved_name));
    ESP_ERROR_CHECK(xTaskCreate(notify_task, "ble_notify", 4096, NULL, 4, &notify_worker) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    nimble_port_freertos_init(host_task);
}

bool ble_bridge_uart_publish(const bridge_packet_t *packet) {
    if(!uart_bridge_selected() || !ble_bridge_connected() || !notify_queue){
        wifi_diag_drop(WD_DROP_INACTIVE,packet);return false;
    }
    if(enqueue_packet(notify_queue,packet))return true;
    atomic_fetch_add(&dropped,1);return false;
}
