#include "wifi_uart_diag.h"
#include <string.h>
#include <stdatomic.h>
#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "esp_timer.h"
static portMUX_TYPE guard=portMUX_INITIALIZER_UNLOCKED;
#define LOCK() portENTER_CRITICAL(&guard)
#define UNLOCK() portEXIT_CRITICAL(&guard)
#else
/* Host unit tests use the same model, with a host clock and lock. */
extern int64_t esp_timer_get_time(void);
static atomic_flag guard=ATOMIC_FLAG_INIT;
#define LOCK() while(atomic_flag_test_and_set(&guard)){}
#define UNLOCK() atomic_flag_clear(&guard)
#endif
static wifi_diag_snapshot_t state;
static atomic_bool active;
static uint32_t sequence;
static uint32_t now_ms(void){return (uint32_t)(esp_timer_get_time()/1000);}
static void assign(unsigned field,uint32_t value,uint32_t seq){
    state.records[field]=(wifi_diag_record_t){value,now_ms(),seq};
}
void wifi_diag_boot(uint32_t id){
    LOCK();state.boot_id=id;
    const unsigned unknown[]={WD_RING_LOST_BYTES,WD_TCP_KERNEL_SEND_HIGH_BYTES,
        WD_WIFI_RATE_KBPS,WD_WIFI_RETRIES,WD_WIFI_PHY_MODE};
    for(unsigned i=0;i<sizeof(unknown)/sizeof(*unknown);i++)state.records[unknown[i]].value=WD_UNKNOWN;
    state.records[WD_WIFI_RSSI].value=(uint32_t)INT32_MIN;
    UNLOCK();
}
void wifi_diag_mode(unsigned mode){
    LOCK();assign(WD_MODE,mode,sequence);active=mode==1;UNLOCK();
}
void wifi_diag_add(unsigned field,uint32_t n,uint32_t seq){
    if(field>=WD_FIELDS||!n||!atomic_load(&active))return;
    LOCK();if(active)assign(field,state.records[field].value+n,seq==WD_NOW_SEQUENCE?sequence:seq);UNLOCK();
}
void wifi_diag_set(unsigned field,uint32_t value,uint32_t seq){
    if(field>=WD_FIELDS||!atomic_load(&active))return;
    LOCK();
    if(active){
        if(field==WD_UART_SEQUENCE)sequence=value;
        assign(field,value,seq==WD_NOW_SEQUENCE?sequence:seq);
    }
    UNLOCK();
}
void wifi_diag_max(unsigned field,uint32_t value){
    if(field>=WD_FIELDS||!atomic_load(&active))return;
    LOCK();if(active&&value>state.records[field].value)assign(field,value,sequence);UNLOCK();
}
void wifi_diag_queue(unsigned depth,unsigned high,unsigned value){
    if(depth>=WD_FIELDS||high>=WD_FIELDS||!atomic_load(&active))return;
    LOCK();if(active){
        if(state.records[depth].value!=value)assign(depth,value,sequence);
        if(value>state.records[high].value)assign(high,value,sequence);
    }UNLOCK();
}
void wifi_diag_drop(unsigned field,const bridge_packet_t *p){
    if(field>=WD_FIELDS||!atomic_load(&active))return;
    if(p->bytes[0]==1&&p->bytes[1]==UART_RX_EVENT&&p->bytes[2]>0&&
       p->bytes[2]<=UART_WIRE_BYTES&&!p->bytes[3]){
        LOCK();if(active)assign(field,state.records[field].value+p->bytes[2],wire_u32(p->bytes+16));UNLOCK();
    }
}
void wifi_diag_snapshot(wifi_diag_snapshot_t *out){
    LOCK();assign(WD_SAMPLE_MS,now_ms(),sequence);state.serial++;*out=state;UNLOCK();
}
bool wifi_diag_query_valid(const uint8_t p[20]){
    if(p[0]!=1||p[1]!=WD_QUERY||p[2]!=WD_QUERY_OP||!wire_u32(p+16))return false;
    for(unsigned i=3;i<16;i++)if(p[i])return false;
    return true;
}
size_t wifi_diag_frames(const wifi_diag_snapshot_t *s,uint32_t token,uint8_t *out,size_t capacity){
    const size_t n=(WD_FIELDS+1)*20;
    if(!token||capacity<n)return 0;
    for(unsigned i=0;i<=WD_FIELDS;i++){
        uint8_t *p=out+i*20;memset(p,0,20);
        p[0]=1;p[1]=WD_EVENT;p[2]=i?i-1:WD_HEADER;p[3]=WD_FIELDS;
        if(!i){wire_put_u32(p+4,WD_VERSION);wire_put_u32(p+8,s->boot_id);wire_put_u32(p+12,s->serial);}
        else {const wifi_diag_record_t *r=&s->records[i-1];
            wire_put_u32(p+4,r->value);wire_put_u32(p+8,r->at_ms);wire_put_u32(p+12,r->uart_sequence);}
        wire_put_u32(p+16,token);
    }
    return n;
}
