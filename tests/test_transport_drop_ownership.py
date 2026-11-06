"""Inject accepted USB/TLS delivery failures; count packet and UART byte owners separately."""
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def function(source,marker):
    start=source.index(marker);opening=source.index('{',start);depth=0
    for end in range(opening,len(source)):
        depth+=(source[end]=='{')-(source[end]=='}')
        if not depth:return source[start:end+1]
    raise AssertionError(marker)

network=(ROOT/'firmware/main/network_link.c').read_text()
bridge=(ROOT/'firmware/main/ble_bridge.c').read_text()
uart=(ROOT/'firmware/main/uart_bridge.c').read_text()
prelude=r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef struct {uint8_t bytes[20];} bridge_packet_t;
typedef struct {bridge_packet_t packets[8];unsigned head,count;} queue_t;
typedef queue_t *QueueHandle_t;
typedef struct {int unused;} esp_tls_t;
enum {pdTRUE=1,UART_RX_EVENT=19,UART_WIRE_BYTES=12,UD_DOWNSTREAM_DROP_BYTES,
 WD_UART_DROP_BYTES,WD_DROP_TLS,WD_DROP_LAN_RESET};
static atomic_uint dropped,drop_total,network_epoch;
static bool wifi_mode=true,change_mode_in_write;
static bool network_wifi_mode(void){return wifi_mode;}
static unsigned ud_bytes,wifi_bytes,diag_packets,completed_bytes;
static bool write_ok;
static void ud_add(unsigned kind,unsigned n){assert(kind==UD_DOWNSTREAM_DROP_BYTES);ud_bytes+=n;}
static void wifi_diag_drop(unsigned kind,const bridge_packet_t *p){
 if(kind==WD_UART_DROP_BYTES)wifi_bytes+=p->bytes[2];else diag_packets++;
}
static bool tls_write_count(esp_tls_t *tls,const uint8_t *p,size_t n,size_t *done){
 (void)tls;(void)p;if(change_mode_in_write)atomic_fetch_add(&network_epoch,1);assert(completed_bytes<=n);*done=completed_bytes;return write_ok;
}
static int xQueueReceive(QueueHandle_t q,bridge_packet_t *p,unsigned wait){
 assert(!wait);if(q->head==q->count)return 0;*p=q->packets[q->head++];return pdTRUE;
}
static void reset(void){dropped=drop_total=network_epoch=0;wifi_mode=true;change_mode_in_write=false;ud_bytes=wifi_bytes=diag_packets=0;write_ok=false;}
'''
functions='\n'.join([function(uart,'void uart_bridge_drop_packet('),function(bridge,'void ble_bridge_delivery_failed('),*[function(network,m)for m in ['static bool tls_write_batch(','static void discard_network_queue(','static void discard_failed_network_queue(']]])
main=r'''
int main(void){
 bridge_packet_t frames[5];memset(frames,0,sizeof(frames));
 for(unsigned i=0;i<5;i++){frames[i].bytes[0]=1;frames[i].bytes[1]=i%2?19:1;frames[i].bytes[2]=i+1;}
 for(unsigned bytes=0;bytes<=sizeof(frames);bytes++){
  reset();completed_bytes=bytes;write_ok=bytes==sizeof(frames);
  assert(tls_write_batch(NULL,(uint8_t*)frames,5,0)==write_ok);
  unsigned lost=write_ok?0:5-bytes/20,uart=0;
  if(!write_ok)for(unsigned i=bytes/20;i<5;i++)if(i%2)uart+=i+1;
  assert(dropped==lost&&diag_packets==lost&&drop_total==uart&&ud_bytes==uart&&wifi_bytes==uart);
 }
 // Cancel while TLS is yielding, or observe an already changed mode/epoch.
 for(unsigned cause=0;cause<3;cause++){
  reset();completed_bytes=10;
  if(cause==0)change_mode_in_write=true;
  else if(cause==1)wifi_mode=false;
  else network_epoch=1;
  assert(!tls_write_batch(NULL,(uint8_t*)frames,5,0));
  assert(dropped==0&&diag_packets==5&&drop_total==6&&ud_bytes==6&&wifi_bytes==6);
 }
 for(unsigned failed=0;failed<2;failed++){
  reset();queue_t q={.count=5};memcpy(q.packets,frames,sizeof(frames));
  if(failed)discard_failed_network_queue(&q);else discard_network_queue(&q);
  assert(q.head==5&&dropped==(failed?5:0)&&drop_total==6&&diag_packets==5);
  // A second cleanup cannot count or replay packets already consumed.
  if(failed)discard_failed_network_queue(&q);else discard_network_queue(&q);
  assert(dropped==(failed?5:0)&&drop_total==6&&diag_packets==5);
 }
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='transport-drop-')as d:
    p=Path(d);(p/'test.c').write_text(prelude+functions+main)
    for mode,flags in [('normal',[]),('sanitized',['-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(p/'test.c'),'-o',str(p/mode)],check=True)
        subprocess.run([str(p/mode)],check=True,timeout=10)
print('PASS: production delivery ownership, all 101 TLS byte boundaries, intentional mode-change race, reset and single cleanup; ASan/UBSan')
