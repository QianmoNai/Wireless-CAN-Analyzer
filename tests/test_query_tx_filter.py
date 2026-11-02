"""Exercise the actual BLE filter/publisher with host queues, without NimBLE.

Extract only the two production functions so the harness tests their real
decisions without copying queue or subscription policy into a test model.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "firmware/main/ble_bridge.c").read_text()


def function(marker):
    start = SOURCE.index(marker)
    opening = SOURCE.index("{", start)
    depth = 0
    for offset in range(opening, len(SOURCE)):
        depth += (SOURCE[offset] == "{") - (SOURCE[offset] == "}")
        if depth == 0:
            return SOURCE[start:offset + 1]
    raise AssertionError("unclosed production function")


PRELUDE = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include "home_status.h"
#include "ack_delivery.h"
#include "uart_protocol.h"
static bool selected_uart;
bool uart_bridge_selected(void){return selected_uart;}
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
struct queue { unsigned count,limit; bridge_packet_t last; };
static struct queue raw={.limit=2},control={.limit=16},acks={.limit=16};
static QueueHandle_t notify_queue=&raw,control_notify_queue=&control,ack_notify_queue=&acks;
static home_status_t home_status;
static int home_lock;
static atomic_bool subscribed;
static atomic_uint dropped;
bool ble_bridge_connected(void){return atomic_load(&subscribed);}
int64_t esp_timer_get_time(void){return 0;}
BaseType_t xQueueSend(QueueHandle_t q,const void *p,unsigned wait){
    if(q->count==q->limit)return pdFALSE;
    q->count++;q->last=*(const bridge_packet_t*)p;return pdTRUE;
}
static void reset(unsigned mode){
    memset(&home_status,0,sizeof(home_status));home_status.mode=mode;
    raw.count=control.count=acks.count=0;
    atomic_store(&subscribed,true);atomic_store(&dropped,0);
}
'''

TEST = r'''
int main(void){
    reset(1);selected_uart=true;
    bridge_packet_t serial={.bytes={1,19,3,0,0,128,255}};
    ble_bridge_publish(&serial);assert(raw.count==1);
    bridge_packet_t can={.bytes={1,1,0,1}};ble_bridge_publish(&can);assert(raw.count==1);
    selected_uart=false;ble_bridge_publish(&serial);assert(raw.count==1);
    // Full channel mode passes every received identifier, regardless of roster.
    reset(0);raw.limit=16;
    const unsigned ids[]={0x201,0x202,0x205,0x7ff};
    for(unsigned i=0;i<4;i++){
        bridge_packet_t rx={.bytes={1,1,0,8}};wire_put_u32(rx.bytes+4,ids[i]);
        ble_bridge_publish(&rx);assert(raw.count==i+1&&wire_u32(raw.last.bytes+4)==ids[i]);
    }
    raw.limit=2;
    // Periodic rates use the bounded status queue, only in full monitoring.
    bridge_packet_t rates={.bytes={1,14}};wire_put_u32(rates.bytes+16,1000000);
    for(unsigned mode=0;mode<=3;mode++){
        reset(mode);ble_bridge_publish(&rates);
        assert(control.count==(mode==0)&&raw.count==0&&acks.count==0);
        assert(atomic_load(&dropped)==0);
    }
    reset(0);assert(!home_redundant(&rates));home_status.mode=1;assert(home_redundant(&rates));
    // Home temperature summaries are control telemetry only for their current
    // quiet-mode revision, and are rechecked before queued delivery.
    bridge_packet_t temperatures={.bytes={1,16,0,1}};wire_put_u32(temperatures.bytes+16,7);
    for(unsigned mode=0;mode<=3;mode++){
        reset(mode);home_status.revision=7;ble_bridge_publish(&temperatures);
        assert(control.count==(mode==1)&&raw.count==0&&acks.count==0&&atomic_load(&dropped)==0);
        home_status.mode=2;assert(home_redundant(&temperatures));
    }
    reset(1);home_status.revision=8;ble_bridge_publish(&temperatures);
    assert(control.count==0&&atomic_load(&dropped)==0&&home_redundant(&temperatures));
    bridge_packet_t query={.bytes={1,2,0,4}};
    wire_put_u32(query.bytes+4,0x7ff);memcpy(query.bytes+8,(uint8_t[]){1,0,0xcc,0},4);
    for(unsigned mode=0;mode<=3;mode++)for(unsigned type=2;type<=3;type++){
        reset(mode);query.bytes[1]=type;query.bytes[2]=type==3?0x44:0; // real ACK + driver error flags
        ble_bridge_publish(&query);
        assert(raw.count==(mode==0)&&control.count==0&&acks.count==0);
        assert(atomic_load(&dropped)==0); // suppression is deliberate, not loss
        if(mode==0)assert(!memcmp(&raw.last,&query,sizeof(query)));
        bridge_packet_t ack={.bytes={1,8}};wire_put_u32(ack.bytes+16,42);
        ble_bridge_publish(&ack);assert(acks.count==1&&control.count==0);
        bridge_packet_t terminal={.bytes={1,8,4}};ble_bridge_publish(&terminal);
        assert(control.count==1); // hidden channels still receive terminal state
        reset(mode);wire_put_u32(query.bytes+16,99);ble_bridge_publish(&query);
        assert(control.count==(mode==0)&&raw.count==0&&acks.count==0);
        wire_put_u32(query.bytes+16,0);
    }
    reset(0);query.bytes[1]=2;query.bytes[2]=0;
    for(unsigned i=0;i<100;i++)ble_bridge_publish(&query);
    assert(raw.count==2&&atomic_load(&dropped)==98&&control.count==0&&acks.count==0);
    bridge_packet_t ack={.bytes={1,8}};wire_put_u32(ack.bytes+16,77);
    ble_bridge_publish(&ack);assert(acks.count==1&&atomic_load(&dropped)==98);
    wire_put_u32(query.bytes+16,88);ble_bridge_publish(&query);
    assert(control.count==1&&acks.count==1&&raw.count==2&&atomic_load(&dropped)==98);
    reset(0);atomic_store(&subscribed,false);wire_put_u32(query.bytes+16,0);
    ble_bridge_publish(&query);assert(raw.count==0&&atomic_load(&dropped)==0);
    puts("PASS: real BLE publisher/filter, tokenless query TX raw routing, mode0-only telemetry, preserved token-bearing control/management ACK routing and raw saturation isolation.");
}
'''

with tempfile.TemporaryDirectory(prefix="can-query-filter-") as temporary:
    harness = Path(temporary) / "filter.c"
    binary = Path(temporary) / "filter"
    harness.write_text(PRELUDE + function("static bool home_redundant(") + "\n" + function("void ble_bridge_publish(") + TEST)
    subprocess.run(["cc", "-std=gnu11", "-O2", "-I" + str(ROOT / "tests/stubs"), "-I" + str(ROOT / "firmware/main"), str(harness), str(ROOT / "firmware/main/home_status.c"), str(ROOT / "firmware/main/ack_delivery.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
