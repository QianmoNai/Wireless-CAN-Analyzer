// Drive the real UART worker one turn at a time; no physical UART is claimed.
#include "sim.h"
#include "../firmware/main/uart_bridge.c"
#include <stdio.h>
struct queue {unsigned count,limit;size_t size;uint8_t *data;};
static struct queue *allocated[64];static unsigned allocated_count;
QueueHandle_t xQueueCreate(unsigned n,size_t size){struct queue*q=calloc(1,sizeof(*q));q->limit=n;q->size=size;q->data=calloc(n,size);allocated[allocated_count++]=q;return q;}
int xQueueSend(QueueHandle_t q,const void*p,unsigned w){(void)w;if(q->count==q->limit)return 0;memcpy(q->data+q->size*q->count++,p,q->size);return 1;}
int xQueueReceive(QueueHandle_t q,void*p,unsigned w){(void)w;if(!q->count)return 0;memcpy(p,q->data,q->size);memmove(q->data,q->data+q->size,--q->count*q->size);return 1;}
void xQueueReset(QueueHandle_t q){q->count=0;}
int xTaskCreate(void(*f)(void*),const char*n,unsigned s,void*a,unsigned p,TaskHandle_t*h){(void)f;(void)n;(void)s;(void)a;(void)p;*h=(void*)1;return 1;}
void xTaskNotifyGive(TaskHandle_t h){(void)h;}
unsigned ulTaskNotifyTake(int c,unsigned w){(void)c;(void)w;return 0;}
int64_t esp_timer_get_time(void){return 1000000;}
static bool link=true,dual=false,admit=true,busy,fail_open,fail_write;
static uint32_t peer_epoch;static unsigned baud_set,opens,closes;
static uint8_t input[512],output[512];static size_t input_count,output_count;
static bridge_packet_t published[256];static unsigned published_count;
bool network_dual_mode(void){return dual;}
bool ble_bridge_connected(void){return link;}
bool dual_link_connected(void){return link;}
void dual_link_set_uart(bool u){(void)u;}
void dual_link_reset_data(void){peer_epoch++;}
uint32_t dual_link_generation(void){return peer_epoch;}
uint32_t dual_link_uart_dropped(void){return 0;}
bool dual_link_uart_rx(const uint8_t*d,size_t n){(void)d;(void)n;return admit;}
bool dual_link_uart_take(uint8_t*d,size_t*n){(void)d;(void)n;return false;}
void ble_bridge_publish(const bridge_packet_t*p){assert(published_count<256);published[published_count++]=*p;}
bool ble_bridge_uart_publish(const bridge_packet_t*p){if(admit)ble_bridge_publish(p);return admit;}
int uart_param_config(int p,const uart_config_t*c){assert(p==1&&c->data_bits==8&&!c->parity&&c->stop_bits==1&&!c->flow_ctrl);baud_set=c->baud_rate;return fail_open?1:0;}
int uart_set_pin(int p,int t,int r,int a,int b){assert(p==1&&t==43&&r==44&&a==-1&&b==-1);return 0;}
int uart_driver_install(int p,int rx,int tx,int n,QueueHandle_t*q,int flags){assert(p==1&&rx==4096&&tx==0&&flags==0);*q=xQueueCreate(n,sizeof(uart_event_t));opens++;return 0;}
int uart_driver_delete(int p){assert(p==1);closes++;busy=false;return 0;}
int uart_set_rx_timeout(int p,int n){assert(p==1&&n==2);return 0;}
int uart_flush_input(int p){assert(p==1);input_count=0;return 0;}
int uart_get_buffered_data_len(int p,size_t*n){assert(p==1);*n=input_count;return 0;}
int uart_read_bytes(int p,void*d,unsigned n,unsigned wait){assert(p==1&&wait==0);if(n>input_count)n=input_count;memcpy(d,input,n);memmove(input,input+n,input_count-n);input_count-=n;return n;}
int uart_tx_chars(int p,const char*d,unsigned n){assert(p==1);if(fail_write)return -1;if(busy)return 0;if(n>7)n=7;memcpy(output+output_count,d,n);output_count+=n;busy=true;return n;}
int uart_wait_tx_done(int p,unsigned w){assert(p==1&&w==0);return busy?1:0;}
static bridge_packet_t command(unsigned len,unsigned token){bridge_packet_t p={.bytes={1,UART_SEND_COMMAND,len}};for(unsigned i=0;i<len&&i<12;i++)p.bytes[4+i]=i?i:0xff;wire_put_u32(p.bytes+16,token);return p;}
int main(void){
 uart_bridge_init();step();assert(!installed&&!uart_bridge_selected());
 bridge_packet_t c=command(12,10);assert(!uart_bridge_send(c.bytes));
 uart_bridge_configure(true,115200);step();assert(installed&&baud_set==115200&&opens==1);
 c=command(13,1);assert(!uart_bridge_send(c.bytes));c=command(1,0);assert(!uart_bridge_send(c.bytes));c=command(1,1);c.bytes[5]=9;assert(!uart_bridge_send(c.bytes));
 for(unsigned i=0;i<256;i++)input[i]=i;
 input_count=256;step();
 assert(input_count==0&&rx_total==256&&published_count==22);
 unsigned offset=0;for(unsigned i=0;i<published_count;i++){bridge_packet_t *p=&published[i];assert(p->bytes[1]==19&&wire_u32(p->bytes+16)==i+1);for(unsigned j=0;j<p->bytes[2];j++)assert(p->bytes[4+j]==(uint8_t)offset++);}assert(offset==256);
 published_count=0;c=command(12,10);assert(uart_bridge_send(c.bytes));step();assert(output_count==7&&!published_count);
 busy=false;step();assert(output_count==12&&!published_count);busy=false;step();assert(published_count==1&&published[0].bytes[1]==20&&published[0].bytes[2]==0&&wire_u32(published[0].bytes+16)==10&&tx_total==12);
 // Mode and connection changes invalidate queued commands before output.
 published_count=0;assert(uart_bridge_send(c.bytes));uart_bridge_configure(false,115200);step();assert(output_count==12&&!installed&&closes==1&&published[0].bytes[2]==3);
 uart_bridge_configure(true,9600);step();assert(baud_set==9600);
 published_count=0;assert(uart_bridge_send(c.bytes));uart_bridge_disconnect();step();assert(output_count==12&&!published_count);
 c=command(3,11);assert(uart_bridge_send(c.bytes));fail_write=true;step();fail_write=false;
 assert(published_count==1&&published[0].bytes[2]==2&&!tx_size&&error_total==1);
 // Backpressure, disconnected RX and hardware errors all remain visible.
 unsigned dropped=drop_total;admit=false;input_count=5;step();assert(drop_total==dropped+5);admit=true;
 dropped=drop_total;link=false;input_count=4;step();assert(drop_total==dropped+4);link=true;
 uart_event_t e={.type=UART_FIFO_OVF};xQueueSend(events,&e,0);input_count=9;step();assert(error_total==2&&input_count==0);
 uart_bridge_configure(false,9600);step();fail_open=true;uart_bridge_configure(true,19200);step();assert(!ready&&error_total==3);
 publish_status();assert(published[published_count-1].bytes[1]==21&&published[published_count-1].bytes[3]==1);
 for(unsigned i=0;i<allocated_count;i++){free(allocated[i]->data);free(allocated[i]);}
 puts("UART bridge: binary RX, pinned UART1, FIFO completion ACK, mode/disconnect isolation, malformed commands, queue backpressure and hardware error reporting PASS");
}
