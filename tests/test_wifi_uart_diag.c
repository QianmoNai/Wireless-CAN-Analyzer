#include "wifi_uart_diag.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static int64_t now;
int64_t esp_timer_get_time(void){return now;}
int main(void){
    wifi_diag_boot(1234);wifi_diag_mode(1);now=100000;
    wifi_diag_set(WD_UART_SEQUENCE,42,42);
    wifi_diag_add(WD_FIFO_OVERRUN_EVENTS,1,WD_NOW_SEQUENCE);
    wifi_diag_queue(WD_LAN_RAW_DEPTH,WD_LAN_RAW_HIGH,128);
    wifi_diag_queue(WD_LAN_RAW_DEPTH,WD_LAN_RAW_HIGH,0);
    bridge_packet_t p={.bytes={1,19,7}};wire_put_u32(p.bytes+16,41);
    wifi_diag_drop(WD_DROP_TLS,&p);p.bytes[1]=20;wifi_diag_drop(WD_DROP_TLS,&p);
    now=200000;
    wifi_diag_snapshot_t s;wifi_diag_snapshot(&s);
    assert(s.boot_id==1234&&s.serial==1);
    assert(s.records[WD_FIFO_OVERRUN_EVENTS].value==1);
    assert(s.records[WD_FIFO_OVERRUN_EVENTS].at_ms==100&&s.records[WD_FIFO_OVERRUN_EVENTS].uart_sequence==42);
    assert(s.records[WD_DROP_TLS].value==7&&s.records[WD_DROP_TLS].uart_sequence==41);
    assert(s.records[WD_LAN_RAW_DEPTH].value==0&&s.records[WD_LAN_RAW_HIGH].value==128);
    assert(s.records[WD_SAMPLE_MS].value==200&&s.records[WD_RING_LOST_BYTES].value==WD_UNKNOWN);
    assert(s.records[WD_WIFI_RATE_KBPS].value==WD_UNKNOWN&&s.records[WD_WIFI_RETRIES].value==WD_UNKNOWN);
    uint8_t request[20]={1,WD_QUERY,WD_QUERY_OP};wire_put_u32(request+16,10);
    assert(wifi_diag_query_valid(request));
    for(unsigned i=3;i<16;i++){request[i]=1;assert(!wifi_diag_query_valid(request));request[i]=0;}
    request[2]=0;assert(!wifi_diag_query_valid(request));request[2]=WD_QUERY_OP;
    wire_put_u32(request+16,0);assert(!wifi_diag_query_valid(request));
    uint8_t out[(WD_FIELDS+1)*20+2];memset(out,'!',sizeof(out));
    assert(!wifi_diag_frames(&s,10,out+1,sizeof(out)-3));
    assert(out[0]=='!'&&out[sizeof(out)-1]=='!');
    size_t n=wifi_diag_frames(&s,10,out+1,sizeof(out)-2);assert(n==(WD_FIELDS+1)*20);
    assert(out[0]=='!'&&out[n+1]=='!');
    for(unsigned i=0;i<=WD_FIELDS;i++){
        uint8_t *f=out+1+20*i;
        assert(f[0]==1&&f[1]==WD_EVENT&&f[2]==(i?i-1:WD_HEADER)&&f[3]==WD_FIELDS&&wire_u32(f+16)==10);
    }
    wifi_diag_mode(0);wifi_diag_add(WD_FIFO_OVERRUN_EVENTS,1,WD_NOW_SEQUENCE);
    wifi_diag_snapshot(&s);assert(s.records[WD_FIFO_OVERRUN_EVENTS].value==1&&s.serial==2);
    wifi_diag_mode(1);wifi_diag_set(WD_UART_RX_BYTES,UINT32_MAX,WD_NOW_SEQUENCE);
    wifi_diag_add(WD_UART_RX_BYTES,2,WD_NOW_SEQUENCE);wifi_diag_snapshot(&s);
    assert(s.records[WD_UART_RX_BYTES].value==1); // Documented modulo arithmetic.
    printf("Wi-Fi numeric diagnostics: coherent snapshots, timestamps/sequence, unavailable values, strict query and %zu B bounded framing PASS\n",n);
}
