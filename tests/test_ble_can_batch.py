"""Compile the real CAN batch drain with bounded queues and MTU/fault injection."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'firmware/main/ble_bridge.c').read_text()
start = source.index('static void deliver_can_batch(')
opening = source.index('{', start)
depth = 0
for end in range(opening, len(source)):
    depth += (source[end] == '{')-(source[end] == '}')
    if not depth:
        function = source[start:end+1]
        break

prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdatomic.h>
typedef struct {uint8_t bytes[20];} bridge_packet_t;
typedef struct {bridge_packet_t packet;uint32_t generation;} notify_item_t;
typedef struct {const bridge_packet_t *packet;const uint8_t *bytes;unsigned size;} notify_payload_t;
typedef unsigned TickType_t;
typedef struct {void *allocate,*pool_free,*release,*submit;} bridge_notify_resource_ops_t;
static atomic_uint connection=1,dropped,notify_generation=1,ble_generation=1;
static atomic_bool batch_subscribed=true;
static unsigned mtu=256,queued[3],head[3],taken,waited,sent,failed,delays,clock_us,calls;
static unsigned sent_class[3],failed_class[3],accept_at=1;
static bool connected=true,change_generation;
static notify_item_t queue[3][32];
static uint8_t output[240];static unsigned output_size;
static int notify_queue=0,control_notify_queue=1,ack_notify_queue=2;
#define notify_allocate NULL
#define notify_pool_free NULL
#define notify_release NULL
#define notify_submit NULL
enum {BD_RAW,BD_CONTROL,BD_ACK,BD_CLASSES};
enum {BD_SUBMITTED,BD_FAILED_PACKETS};
#define bd_index(cls,word) ((cls)*2+(word))
static void bd_add(unsigned word,unsigned n){if(word%2==BD_SUBMITTED){sent+=n;sent_class[word/2]+=n;}else{failed+=n;failed_class[word/2]+=n;}}
static unsigned packet_class(const bridge_packet_t*p){return p->bytes[1]==8?BD_ACK:p->bytes[1]==4?BD_CONTROL:BD_RAW;}
static unsigned ble_att_mtu(unsigned conn){assert(conn==1);return mtu;}
static unsigned packet_generation(const bridge_packet_t*p){return atomic_load(p->bytes[1]==17?&ble_generation:&notify_generation);}
static bool packet_connected(const bridge_packet_t*p){(void)p;return connected;}
static bool suppress_packet(const bridge_packet_t*p){return p->bytes[1]==99;}
static void uart_bridge_drop_packet(const bridge_packet_t*p){(void)p;}
static int64_t esp_timer_get_time(void){return clock_us++;}
static bool dequeue_item(int q,notify_item_t*p,unsigned wait){waited+=wait;clock_us+=wait*10000;if(!queued[q])return false;*p=queue[q][head[q]++];queued[q]--;taken++;return true;}
#define pdMS_TO_TICKS(ms) (ms)
static void vTaskDelay(unsigned ms){delays+=ms;}
static void notify_delay(void *context,unsigned milliseconds){assert(*(uint32_t*)context==1);delays+=milliseconds;if(change_generation)atomic_fetch_add(&notify_generation,1);}
static bool bridge_notify_with_reserve(const bridge_packet_t*p,const bridge_notify_resource_ops_t*ops,void*ctx){
 (void)ops;notify_payload_t*v=ctx;assert(v->size<=240&&v->size%20==0);assert(v->size<=mtu-3);calls++;
 if(!accept_at||calls<accept_at)return false;
 memcpy(output,v->bytes,v->size);output_size=v->size;bd_add(bd_index(packet_class(p),BD_SUBMITTED),1);return true;
}
static notify_item_t frame(unsigned n,unsigned kind){notify_item_t p={.generation=1};p.packet.bytes[0]=1;p.packet.bytes[1]=kind;p.packet.bytes[4]=n;return p;}
static void reset(unsigned count){memset(queued,0,sizeof(queued));memset(head,0,sizeof(head));memset(sent_class,0,sizeof(sent_class));memset(failed_class,0,sizeof(failed_class));queued[0]=count;taken=waited=sent=failed=delays=clock_us=output_size=calls=0;atomic_store(&dropped,0);connected=true;accept_at=1;change_generation=false;atomic_store(&notify_generation,1);atomic_store(&ble_generation,1);for(unsigned i=0;i<count;i++)queue[0][i]=frame(i+1,1);}
'''
main = r'''
int main(void){
 notify_item_t first=frame(0,1);
 for(unsigned size=23;size<=256;size++){
  mtu=size;reset(20);deliver_can_batch(&first);unsigned expected=(mtu-3)/20;if(expected>10)expected=10;
  assert(output_size==expected*20&&taken==expected-1&&sent==expected);
  for(unsigned i=0;i<expected;i++)assert(output[i*20]==1&&output[i*20+4]==i);
 }
 mtu=256;reset(0);deliver_can_batch(&first);assert(output_size==20&&waited==1);
 reset(20);queued[BD_ACK]=1;queue[BD_ACK][0]=frame(90,8);queued[BD_CONTROL]=1;queue[BD_CONTROL][0]=frame(80,4);
 deliver_can_batch(&first);assert(output_size==200&&taken==9&&waited==0);
 assert(output[24]==90&&output[44]==80&&sent_class[BD_ACK]==1&&sent_class[BD_CONTROL]==1&&sent_class[BD_RAW]==8);
 reset(0);notify_item_t ack=frame(90,8);deliver_can_batch(&ack);assert(output_size==20&&waited==0&&sent_class[BD_ACK]==1);
 reset(20);accept_at=0;deliver_can_batch(&first);assert(failed==10&&atomic_load(&dropped)==10&&delays==80&&calls==8);
 reset(0);accept_at=0;deliver_can_batch(&ack);assert(failed_class[BD_ACK]==1&&delays==80&&calls==8);
 reset(20);accept_at=3;deliver_can_batch(&first);assert(sent==10&&!failed&&calls==3&&delays==20&&!atomic_load(&dropped));
 reset(20);atomic_store(&notify_generation,2);deliver_can_batch(&first);assert(output_size==0&&!taken);
 reset(20);connected=false;deliver_can_batch(&first);assert(output_size==0);
 reset(20);accept_at=0;change_generation=true;deliver_can_batch(&first);assert(calls==1&&!failed&&!output_size);
 reset(32);for(unsigned i=0;i<32;i++)queue[0][i].packet.bytes[1]=99;
 deliver_can_batch(&first);assert(taken==23&&sent==1); // invalid/suppressed input cannot cause an unbounded drain
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='can-batch-') as directory:
    path = Path(directory)
    (path/'test.c').write_text(prelude+function+main)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(path/'test.c'), '-o', str(path/'test')], check=True)
    subprocess.run([str(path/'test')], check=True)
print('PASS: actual BLE CAN batching, MTUs 23..256, ACK/control priority, bounded drain/waits, resource recovery and stale generations')
