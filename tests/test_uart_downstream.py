"""Run production UART downstream accounting with real USB queues and fake sinks.

Only platform operations are stubbed; the BLE delivery decision, USB state
machine, UART byte accounting and LAN partial-batch accounting are production.
No physical USB, BLE or LAN evidence is claimed by this host test.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(filename, marker):
    source = (ROOT / "firmware/main" / filename).read_text()
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for offset in range(opening, len(source)):
        depth += (source[offset] == "{") - (source[offset] == "}")
        if not depth:
            return source[start:offset + 1] + "\n"
    raise AssertionError("unclosed production function")


PRELUDE = r'''
#include "sim.h"
#include <stdatomic.h>
#include <stdio.h>
#include "uart_bridge.h"
#include "notify_resource.h"
#include "ack_delivery.h"
struct queue {unsigned count,limit;size_t size;uint8_t *data;};
static struct queue *allocated[8];static unsigned allocated_count;
QueueHandle_t xQueueCreate(unsigned n,size_t size){struct queue*q=calloc(1,sizeof(*q));q->limit=n;q->size=size;q->data=calloc(n,size);allocated[allocated_count++]=q;return q;}
int xQueueSend(QueueHandle_t q,const void*p,unsigned w){(void)w;if(q->count==q->limit)return 0;memcpy(q->data+q->size*q->count++,p,q->size);return 1;}
int xQueueReceive(QueueHandle_t q,void*p,unsigned w){(void)w;if(!q->count)return 0;memcpy(p,q->data,q->size);memmove(q->data,q->data+q->size,--q->count*q->size);return 1;}
void xQueueReset(QueueHandle_t q){q->count=0;}
SemaphoreHandle_t xSemaphoreCreateMutex(void){return (void*)1;}
int xSemaphoreTake(SemaphoreHandle_t q,unsigned w){(void)q;(void)w;return 1;}
int xSemaphoreGive(SemaphoreHandle_t q){(void)q;return 1;}
int xTaskCreate(void(*f)(void*),const char*n,unsigned s,void*a,unsigned p,void*h){(void)f;(void)n;(void)s;(void)a;(void)p;(void)h;return 1;}
static uint32_t now=10;static unsigned mode=4,delays,resets;static bool online=true,sink_success;
void vTaskDelay(unsigned t){now+=t;delays++;}
int64_t esp_timer_get_time(void){return (int64_t)now*1000;}
bool network_usb_mode(void){return mode==4;}
bool network_wifi_mode(void){return mode==1;}
bool usb_serial_jtag_is_connected(void){return true;}
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t*c){(void)c;return 0;}
int usb_serial_jtag_write_bytes(const void*p,size_t n,uint32_t w){(void)p;(void)w;return n;}
int usb_serial_jtag_read_bytes(void*p,uint32_t n,uint32_t w){(void)p;(void)n;(void)w;return 0;}
void ble_bridge_reset_session(void){resets++;}
int ble_bridge_command(const uint8_t p[20]){(void)p;return 0;}
int ble_bridge_settings_command(const uint8_t p[20],bridge_packet_t*r){(void)p;(void)r;return 0;}
static atomic_uint drop_total,dropped;
'''

PLATFORM = r'''
static bool suppressed;
bool ble_bridge_connected(void){return mode==4?usb_link_connected():online;}
static bool suppress_packet(const bridge_packet_t*p){(void)p;return suppressed;}
bool network_send(const bridge_packet_t*p){(void)p;return sink_success;}
static void *notify_allocate(void*c,const bridge_packet_t*p){(void)c;(void)p;return NULL;}
static unsigned notify_pool_free(void*c,void*b){(void)c;(void)b;return 0;}
static void notify_release(void*c,void*b){(void)c;(void)b;}
static bool notify_submit(void*c,void*b){(void)c;(void)b;return sink_success;}
bool bridge_notify_with_reserve(const bridge_packet_t*p,const bridge_notify_resource_ops_t*o,void*c){(void)p;(void)o;(void)c;return sink_success;}
static QueueHandle_t notify_queue;
bool uart_bridge_selected(void){return true;}
typedef int esp_tls_t;
#define ESP_TLS_ERR_SSL_WANT_READ (-2)
#define ESP_TLS_ERR_SSL_WANT_WRITE (-3)
static int write_results[8];static unsigned write_at;
int esp_tls_conn_write(esp_tls_t*t,const uint8_t*p,size_t n){(void)t;(void)p;int r=write_results[write_at++];return r==999?(int)n:r;}
'''

TEST = r'''
int main(void){
 usb_link_init();uint8_t hello[20]={1,USB_HELLO};memcpy(hello+4,"CANUSB1",7);
 assert(incoming(123,hello));
 bridge_packet_t raw={.bytes={1,19,12}},rejected={.bytes={1,19,7}};
 for(unsigned i=0;i<128;i++)assert(usb_link_send(&raw));
 deliver_packet(&rejected,false,false);
 assert(drop_total==7&&dropped==1); // Actual full USB queue rejected seven payload bytes.
 clear_session();assert(drop_total==7+128*12);
 clear_session();assert(drop_total==7+128*12); // Reset cannot count the same queue twice.
 // Successful downstream ownership transfers leave UART counts untouched.
 assert(incoming(124,hello));unsigned before=drop_total;
 deliver_packet(&rejected,false,false);assert(drop_total==before&&raw_out->count==1);
 service(NULL,0);assert(drop_total==before&&raw_out->count==0);
 // One BLE/LAN raw rejection, irrespective of transport, counts exact bytes.
 for(unsigned transport=0;transport<=1;transport++){
  mode=transport;sink_success=false;before=drop_total;
  deliver_packet(&rejected,false,false);assert(drop_total==before+7);
  sink_success=true;before=drop_total;deliver_packet(&rejected,false,false);assert(drop_total==before);
 }
 // Lost ACKs never imply lost physical UART output, including all eight retries.
 bridge_packet_t ack={.bytes={1,20,0,7}};wire_put_u32(ack.bytes+16,19);
 sink_success=false;before=drop_total;deliver_packet(&ack,true,false);assert(drop_total==before);
 online=false;deliver_packet(&rejected,false,false);assert(drop_total==before+7);online=true;
 suppressed=true;before=drop_total;deliver_packet(&rejected,false,false);assert(drop_total==before+7);suppressed=false;
 // First-hop rejection belongs to UART worker, not the publisher; later reset
 // of an accepted packet is downstream loss, exactly once.
 notify_queue=xQueueCreate(1,sizeof(bridge_packet_t));
 assert(ble_bridge_uart_publish(&raw));before=drop_total;
 assert(!ble_bridge_uart_publish(&rejected));assert(drop_total==before);
 discard_raw_queue();assert(drop_total==before+12);discard_raw_queue();assert(drop_total==before+12);
 // LAN batches: count only uncompleted frames and a partial final frame.
 uint8_t batch[60];memcpy(batch,raw.bytes,20);memcpy(batch+20,rejected.bytes,20);memcpy(batch+40,ack.bytes,20);
 before=drop_total;write_at=0;write_results[0]=20;write_results[1]=5;write_results[2]=-1;
 assert(!tls_write_batch(NULL,batch,3));assert(drop_total==before+7);
 before=drop_total;write_at=0;write_results[0]=-1;
 assert(!tls_write_batch(NULL,batch,3));assert(drop_total==before+12+7);
 before=drop_total;write_at=0;write_results[0]=999;
 assert(tls_write_batch(NULL,batch,3));assert(drop_total==before);
 write_at=0;write_results[0]=999;assert(tls_write_all(NULL,batch,60));
 QueueHandle_t lan=xQueueCreate(2,sizeof(bridge_packet_t));xQueueSend(lan,&raw,0);xQueueSend(lan,&ack,0);
 before=drop_total;discard_network_queue(lan);assert(drop_total==before+12);
 discard_network_queue(lan);assert(drop_total==before+12);
 for(unsigned i=0;i<allocated_count;i++){free(allocated[i]->data);free(allocated[i]);}
 puts("PASS: UART downstream exact payload bytes, real USB queue saturation, no rejection/reset double count, BLE/LAN failure, ACK exclusion and LAN partial batch ownership.");
}
'''

with tempfile.TemporaryDirectory(prefix="can-uart-downstream-") as temporary:
    harness = Path(temporary) / "downstream.c"
    binary = Path(temporary) / "downstream"
    source = PRELUDE + function("uart_bridge.c", "void uart_bridge_drop_packet(")
    source += '#include "usb_link.c"\n' + PLATFORM
    for marker in ("static bool notify_connected(", "static bool notify_send(", "static void notify_delay(",
                   "static void deliver_packet(", "static void discard_raw_queue(", "bool ble_bridge_uart_publish("):
        source += function("ble_bridge.c", marker)
    for marker in ("static bool tls_write_count(", "static bool tls_write_all(",
                   "static bool tls_write_batch(", "static void discard_network_queue("):
        source += function("network_link.c", marker)
    harness.write_text(source + TEST)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-I" + str(ROOT / "tests/usb_stubs"),
                    "-I" + str(ROOT / "firmware/main"), str(harness),
                    str(ROOT / "firmware/main/ack_delivery.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
