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
unsigned uxQueueMessagesWaiting(QueueHandle_t q){return q->count;}
int xTaskCreate(void(*f)(void*),const char*n,unsigned s,void*a,unsigned p,TaskHandle_t*h){(void)f;(void)n;(void)s;(void)a;(void)p;*h=(void*)1;return 1;}
static TaskHandle_t registered_owner;
static unsigned notify_gives,notify_takes,notify_wait,delays,yields;
static int64_t time_us=1000000;
static bool locked;
SemaphoreHandle_t xSemaphoreCreateMutex(void){return (void*)2;}
int xSemaphoreTake(SemaphoreHandle_t m,unsigned w){assert(m==port_lock&&w==portMAX_DELAY&&!locked);locked=true;return pdTRUE;}
void xSemaphoreGive(SemaphoreHandle_t m){assert(m==port_lock&&locked);locked=false;}
void dual_link_set_uart_owner(TaskHandle_t h){registered_owner=h;}
void xTaskNotifyGive(TaskHandle_t h){assert(h);notify_gives++;}
unsigned ulTaskNotifyTake(int c,unsigned w){assert(c==pdTRUE&&!locked);notify_takes++;notify_wait=w;return 0;}
void vTaskDelay(unsigned w){assert(w==1&&!locked);delays++;time_us+=portTICK_PERIOD_MS*1000;}
void taskYIELD(void){assert(!locked);yields++;}
int64_t esp_timer_get_time(void){return time_us;}
static bool link=true,dual=false,wifi=false,admit=true,busy,fail_open,fail_write;
static uint32_t peer_epoch;static unsigned baud_set,opens,closes;
static uint8_t input[512],output[2048];static size_t input_count,output_count;
static bridge_packet_t published[256];static unsigned published_count;
static uint8_t dual_data[32][DUAL_UART_CHUNK];static size_t dual_sizes[32];
static unsigned dual_count,dual_taken,take_calls,write_calls,wait_calls;
static unsigned write_limit=7,cancel_on_take,cancel_on_done;
static bool instant_tx,complete_on_wait,fail_wait,rx_during_wait;
static int64_t write_at[128];
static unsigned fifo_bytes;
static void cancel_tx(unsigned action){
 if(action==1)link=false;
 if(action==2)uart_bridge_disconnect();
 if(action==3)uart_bridge_configure(false,115200);
 if(action==4)peer_epoch++;
}
bool network_dual_mode(void){return dual;}
bool network_wifi_mode(void){return wifi;}
bool ble_bridge_connected(void){return link;}
bool dual_link_connected(void){return link;}
void dual_link_set_uart(bool u){(void)u;}
void dual_link_reset_data(void){peer_epoch++;}
uint32_t dual_link_generation(void){return peer_epoch;}
uint32_t dual_link_uart_dropped(void){return 0;}
bool dual_link_uart_rx(const uint8_t*d,size_t n){(void)d;(void)n;return admit;}
bool dual_link_uart_take(uint8_t*d,size_t*n){
 take_calls++;if(dual_taken==dual_count)return false;
 *n=dual_sizes[dual_taken];memcpy(d,dual_data[dual_taken++],*n);
 unsigned action=cancel_on_take;cancel_on_take=0;cancel_tx(action);return true;
}
void ble_bridge_publish(const bridge_packet_t*p){assert(published_count<256);published[published_count++]=*p;}
bool ble_bridge_uart_publish(const bridge_packet_t*p){if(admit)ble_bridge_publish(p);return admit;}
int uart_param_config(int p,const uart_config_t*c){assert(p==1&&c->data_bits==8&&!c->parity&&c->stop_bits==1&&!c->flow_ctrl);baud_set=c->baud_rate;return fail_open?1:0;}
int uart_set_pin(int p,int t,int r,int a,int b){assert(p==1&&t==43&&r==44&&a==-1&&b==-1);return 0;}
int uart_driver_install(int p,int rx,int tx,int n,QueueHandle_t*q,int flags){assert(p==1&&rx==4096&&tx==0&&flags==0);*q=xQueueCreate(n,sizeof(uart_event_t));opens++;return 0;}
int uart_driver_delete(int p){assert(p==1&&locked);closes++;busy=false;return 0;}
int uart_set_rx_timeout(int p,int n){assert(p==1&&n==2);return 0;}
int uart_flush_input(int p){assert(p==1);input_count=0;return 0;}
int uart_get_buffered_data_len(int p,size_t*n){assert(p==1);*n=input_count;return 0;}
int uart_read_bytes(int p,void*d,unsigned n,unsigned wait){assert(p==1&&wait==0);if(n>input_count)n=input_count;memcpy(d,input,n);memmove(input,input+n,input_count-n);input_count-=n;return n;}
int uart_tx_chars(int p,const char*d,unsigned n){
 assert(p==1&&locked);assert(write_calls<128);write_at[write_calls++]=time_us;
 if(fail_write)return -1;
 if(busy)return 0;
 if(n>write_limit)n=write_limit;
 assert(output_count+n<=sizeof(output));memcpy(output+output_count,d,n);output_count+=n;
 fifo_bytes=n;busy=!instant_tx&&n;return n;
}
int uart_wait_tx_done(int p,unsigned w){
 assert(p==1&&locked&&(w==0||w==pdMS_TO_TICKS(20)));wait_calls++;
 if(fail_wait)return 2;
 if(rx_during_wait){
  rx_during_wait=false;unsigned before=rx_total;
  input_count=4;step();assert(rx_total==before+4&&locked);
 }
 if(busy&&w&&complete_on_wait){time_us+=(fifo_bytes*10000000ull+115199)/115200;busy=false;}
 if(busy){if(w)time_us+=20000;return ESP_ERR_TIMEOUT;}
 unsigned action=cancel_on_done;cancel_on_done=0;cancel_tx(action);return ESP_OK;
}
static bridge_packet_t command(unsigned len,unsigned token){bridge_packet_t p={.bytes={1,UART_SEND_COMMAND,len}};for(unsigned i=0;i<len&&i<12;i++)p.bytes[4+i]=i?i:0xff;wire_put_u32(p.bytes+16,token);return p;}
static void prepare_dual(unsigned count,unsigned size){
 dual=true;link=true;fail_open=fail_write=false;busy=false;instant_tx=false;
 cancel_on_take=cancel_on_done=0;complete_on_wait=fail_wait=rx_during_wait=false;dual_count=dual_taken=0;write_limit=DUAL_UART_CHUNK;
 uart_bridge_configure(true,115200);peer_epoch++;step();
 output_count=take_calls=write_calls=wait_calls=0;dual_count=count;
 assert(count<=32&&size<=DUAL_UART_CHUNK);
 for(unsigned i=0;i<count;i++){
  dual_sizes[i]=size;
  for(unsigned j=0;j<size;j++)dual_data[i][j]=(uint8_t)(i*size+j);
 }
}
static void test_dual_tx(void){
 assert(registered_owner==tx_worker);
 // The periodic RX worker cannot drive dual output. New-data notification
 // wakes the dedicated task, whose completions immediately fill the next FIFO.
 prepare_dual(3,64);unsigned sent=tx_total;
 step();assert(output_count==0&&take_calls==0);
 complete_on_wait=true;unsigned idle=notify_takes,delay_before=delays;
 xTaskNotifyGive(registered_owner);dual_tx_iteration();
 assert(output_count==192&&dual_taken==3&&tx_total==sent+192&&!tx_size);
 assert(write_at[1]-write_at[0]==5556&&write_at[2]-write_at[1]==5556);
 assert(delays==delay_before&&notify_takes==idle+1&&notify_wait==portMAX_DELAY);
 for(unsigned i=0;i<192;i++)assert(output[i]==(uint8_t)i);
 // Sustained normal completions cross the four-step budget without a tick
 // delay; RX can still drain while the TX task owns its completion lock.
 prepare_dual(9,64);complete_on_wait=true;rx_during_wait=true;
 delay_before=delays;unsigned yield_before=yields;
 dual_tx_iteration();dual_tx_iteration();dual_tx_iteration();
 assert(output_count==576&&dual_taken==9&&!tx_size&&delays==delay_before&&yields==yield_before+2);
 for(unsigned i=1;i<9;i++)assert(write_at[i]-write_at[i-1]==5556);
 for(unsigned i=0;i<576;i++)assert(output[i]==(uint8_t)i);
 // Empty output performs one empty take then waits indefinitely, no UART
 // calls, periodic timeout, or self-notification. A later wake delivers data.
 prepare_dual(0,64);idle=notify_takes;unsigned gives=notify_gives;
 dual_tx_iteration();assert(notify_takes==idle+1&&notify_wait==portMAX_DELAY);
 assert(!write_calls&&!wait_calls&&notify_gives==gives&&take_calls==1);
 dual_count=1;dual_sizes[0]=3;memcpy(dual_data[0],"abc",3);
 xTaskNotifyGive(registered_owner);complete_on_wait=true;dual_tx_iteration();
 assert(output_count==3&&!memcmp(output,"abc",3)&&!tx_size);
 // Immediate completions hit exactly four advances; no extra dequeue occurs.
 // The actual task yields to the idle task on this pathological fast path.
 prepare_dual(DUAL_TX_STEPS_PER_TURN*2+1,3);instant_tx=true;sent=tx_total;
 idle=notify_takes;delay_before=delays;dual_tx_iteration();
 assert(dual_taken==DUAL_TX_STEPS_PER_TURN&&take_calls==DUAL_TX_STEPS_PER_TURN);
 assert(write_calls==DUAL_TX_STEPS_PER_TURN&&wait_calls==DUAL_TX_STEPS_PER_TURN);
 assert(tx_total==sent+3*DUAL_TX_STEPS_PER_TURN&&delays==delay_before+1&&notify_takes==idle);
 dual_tx_iteration();assert(dual_taken==2*DUAL_TX_STEPS_PER_TURN);
 dual_tx_iteration();assert(dual_taken==dual_count&&!tx_size);
 for(unsigned i=0;i<output_count;i++)assert(output[i]==(uint8_t)i);
 // A 190 B block exceeding the FIFO fills its tail after TX_DONE, then the
 // next block in the same turn. Counts remain whole-block completion counts.
 prepare_dual(2,190);write_limit=127;complete_on_wait=true;sent=tx_total;
 assert(dual_tx_turn());assert(output_count==380&&dual_taken==2&&!tx_size&&tx_total==sent+380);
 assert(write_calls==4&&wait_calls==4);
 for(unsigned i=0;i<380;i++)assert(output[i]==(uint8_t)i);
 // Busy hardware blocks in the driver with a finite cancellation deadline;
 // it cannot consume unbounded work or take the next block on a timeout.
 prepare_dual(2,12);write_limit=7;sent=tx_total;
 assert(dual_tx_turn());assert(output_count==7&&dual_taken==1&&tx_total==sent);
 assert(write_calls==DUAL_TX_STEPS_PER_TURN&&wait_calls==DUAL_TX_STEPS_PER_TURN);
 complete_on_wait=true;assert(dual_tx_turn());assert(!dual_tx_turn());assert(output_count==24&&!tx_size&&tx_total==sent+24);
 for(unsigned i=0;i<24;i++)assert(output[i]==(uint8_t)i);
 // Link loss, explicit disconnect, deselection and peer epoch cancellation
 // during completion or take cannot cross a dequeue/write boundary.
 for(unsigned action=1;action<=4;action++){
  prepare_dual(2,12);complete_on_wait=true;cancel_on_done=action;
  assert(!dual_tx_turn());assert(output_count==12&&dual_taken==1&&!tx_size);
  prepare_dual(2,12);cancel_on_take=action;
  assert(!dual_tx_turn());assert(output_count==0&&dual_taken==1&&tx_size==12);
  dual_count=dual_taken;step();assert(output_count==0&&!tx_size);
 }
 // Cancellation at a partial FIFO completion discards only the unsent tail.
 prepare_dual(2,12);write_limit=7;complete_on_wait=true;cancel_on_done=2;
 unsigned dropped=drop_total;assert(!dual_tx_turn());
 assert(output_count==7&&tx_offset==7&&dual_taken==1);
 dual_count=dual_taken;step();assert(!tx_size&&drop_total==dropped+5);
 // A pending wait also cannot race driver deletion/reopen under port_lock.
 prepare_dual(2,12);complete_on_wait=true;cancel_on_done=3;dual_tx_turn();
 unsigned close_before=closes;step();assert(!installed&&closes==close_before+1);
 prepare_dual(2,12);fail_write=true;dual_tx_turn();assert(!tx_size&&dual_taken==2);
 prepare_dual(2,12);fail_wait=true;dual_tx_turn();assert(!tx_size&&dual_taken==2);
 puts("Dual UART TX: data wake, ISR completion without tick gaps, idle blocking, bounded budget, partial FIFO, order, cancellation and driver lifetime PASS");
}
int main(void){
 wifi_diag_boot(1);wifi_diag_mode(1);wifi=true;
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
 uart_event_t e={.type=UART_FIFO_OVF};xQueueSend(events,&e,0);input_count=9;
 unsigned rx_before=rx_total,published_before=published_count;step();
 assert(error_total==2&&input_count==0&&rx_total==rx_before+9&&published_count==published_before+1);
 // Ring-full and line-error events also preserve valid queued bytes.
 for(unsigned type=UART_BUFFER_FULL;type<=UART_PARITY_ERR;type++){
  e.type=type;xQueueSend(events,&e,0);input_count=7;rx_before=rx_total;step();assert(rx_total==rx_before+7);
 }
 wifi_diag_snapshot_t diag;wifi_diag_snapshot(&diag);
 assert(diag.records[WD_FIFO_OVERRUN_EVENTS].value==1&&diag.records[WD_RING_FULL_EVENTS].value==1);
 assert(diag.records[WD_RING_HIGH_BYTES].value==256&&diag.records[WD_RING_LOST_BYTES].value==WD_UNKNOWN);
 assert(diag.records[WD_UART_RX_BYTES].value==rx_total);
#if CONFIG_BLE_TEST_DIAGNOSTICS
 uint32_t d[UD_WORDS];uart_bridge_diagnostics(d);
 assert(d[UD_FIFO_OVF]==1&&d[UD_ERROR_FLUSHES]==0&&d[UD_ERROR_FLUSH_BYTES]==0);
 assert(d[UD_READ_BYTES]==295&&d[UD_BLE_ATTEMPT_BYTES]==291&&d[UD_BLE_REJECT_BYTES]==5&&d[UD_DISCONNECTED_BYTES]==4);
 assert(d[UD_TX_DISCARD_BYTES]==27);
#endif
 uart_bridge_configure(false,9600);step();fail_open=true;uart_bridge_configure(true,19200);step();assert(!ready&&error_total==6);
 // Downstream accounting accepts only actual UART RX payload bytes and is
 // cumulative across status reads, disconnect and baud/mode changes.
 dropped=drop_total;bridge_packet_t lost={.bytes={1,19,7}};
 uart_bridge_drop_packet(&lost);lost.bytes[2]=12;uart_bridge_drop_packet(&lost);
 lost.bytes[2]=0;uart_bridge_drop_packet(&lost);lost.bytes[2]=13;uart_bridge_drop_packet(&lost);
 lost.bytes[2]=5;lost.bytes[3]=1;uart_bridge_drop_packet(&lost);lost.bytes[3]=0;
 lost.bytes[1]=20;uart_bridge_drop_packet(&lost);lost.bytes[1]=19;lost.bytes[0]=0;uart_bridge_drop_packet(&lost);
 assert(drop_total==dropped+19);
 uart_bridge_stats_t snapshot;uart_bridge_stats(&snapshot);assert(snapshot.dropped==dropped+19);
 uart_bridge_disconnect();uart_bridge_stats(&snapshot);assert(snapshot.dropped==dropped+19);
 publish_status();assert(published[published_count-1].bytes[1]==21&&published[published_count-1].bytes[3]==1);
 test_dual_tx();
 for(unsigned i=0;i<allocated_count;i++){free(allocated[i]->data);free(allocated[i]);}
 puts("UART bridge: binary RX, pinned UART1, FIFO completion ACK, mode/disconnect isolation, malformed commands, queue backpressure and hardware error reporting PASS");
}
