"""Exercise the production collector offline: bounds, partial batch, control first."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'firmware/main/network_link.c').read_text()
collector = 'static unsigned collect_batch(uint8_t *batch){' + source.split(
    'static unsigned collect_batch(uint8_t *batch){', 1)[1].split('\n}', 1)[0] + '\n}\n'
test = r'''
#include "network_batch.h"
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef struct {uint8_t bytes[20];} bridge_packet_t;
typedef struct {unsigned left, consumed; uint8_t tag;} queue_t;
static queue_t control, raw;
static queue_t *control_out=&control, *raw_out=&raw;
static int inject;
static bool uart;
static bool uart_bridge_selected(void){return uart;}
#define pdTRUE 1
static int xQueueReceive(queue_t *q, bridge_packet_t *p, unsigned wait){
 assert(wait==0);
 if(!q->left)return 0;
 --q->left;++q->consumed;memset(p->bytes,q->tag,20);
 if(inject&&q==raw_out){inject=0;control.left=1;}
 return pdTRUE;
}
''' + collector + r'''
int main(void){
 struct {uint8_t before[8],data[20*CAN_WIFI_BATCH_CAPACITY],after[8];} buf;
 for(unsigned mode=0;mode<2;mode++){
 uart=mode;
 memset(&buf,0xa5,sizeof(buf));
 control=(queue_t){3,0,0xc1};raw=(queue_t){100,0,0xd1};
 unsigned n=collect_batch(buf.data);assert(n==(uart?CAN_WIFI_TLS_BATCH:CAN_WIFI_CAN_BATCH));
 assert(control.consumed==3&&raw.consumed==n-3);
 for(unsigned i=0;i<n*20;++i)assert(buf.data[i]==(i<60?0xc1:0xd1));
 for(unsigned i=0;i<8;++i)assert(buf.before[i]==0xa5&&buf.after[i]==0xa5);
 control.left=1;raw.left=2;
 assert(collect_batch(buf.data)==3); // Return immediately without waiting to fill.
 assert(collect_batch(buf.data)==0);
 raw.left=2;inject=1;
 assert(collect_batch(buf.data)==3);
 assert(buf.data[0]==0xd1&&buf.data[20]==0xc1&&buf.data[40]==0xd1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-batch-') as folder:
    path=Path(folder);c=path/'test.c';c.write_text(test)
    for value in [None,16,32,64]:
        flags=[] if value is None else ['-DCAN_WIFI_TLS_BATCH='+str(value)]
        binary=path/str(value)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,
                        '-I'+str(root/'firmware/main'),str(c),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
    for invalid in [0,17,128]:
        result=subprocess.run(['cc','-std=c11','-DCAN_WIFI_TLS_BATCH='+str(invalid),
                               '-I'+str(root/'firmware/main'),'-fsyntax-only',str(c)],
                              capture_output=True)
        assert result.returncode != 0
print('PASS: CAN 64 and UART default/16/32/64 production batches, bounds, partial return, control priority, invalid sizes')
