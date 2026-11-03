// Runs the actual CAN owner loop with simulated BLE, RTOS queues and CAN completions.
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <setjmp.h>
#include "stubs/sim.h"
#include "../firmware/main/ble_bridge.h"
#include "../firmware/main/uart_bridge.h"
void uart_bridge_init(void){}
void device_settings_init(void){}
static bool simulated_uart;
bool uart_bridge_selected(void){return simulated_uart;}
void uart_bridge_stats(uart_bridge_stats_t *s){memset(s,0,sizeof(*s));}
void board_ui_start(void) {}
void board_ui_observe(const bridge_packet_t *packet) { (void)packet; }
void board_status_led_init(void) {}
void board_status_led_update(bool connected,uint32_t rx_count,uint32_t tx_count,uint32_t now_ms)
{ (void)connected;(void)rx_count;(void)tx_count;(void)now_ms; }
// Additional CAN-owner scenario consumes frames with no App connection.
static bool simulated_dual;static unsigned dual_taken;
void dual_link_set_owner(TaskHandle_t owner) {(void)owner;}
void dual_link_can_rx(const uint8_t packet[20]) {(void)packet;}
bool dual_link_can_take(uint8_t packet[20]) {
 if(!simulated_dual||dual_taken>=3)return false;
 memset(packet,0,20);packet[0]=1;packet[1]=0x10;packet[2]=dual_taken;packet[3]=8;
 wire_put_u32(packet+4,0x321+dual_taken);memset(packet+8,dual_taken+1,8);dual_taken++;return true;
}
bool dual_link_connected(void) {return simulated_dual;}
bool network_dual_mode(void) {return simulated_dual;}
uint32_t dual_link_dropped(void) {return 0;}
struct queue {size_t size;unsigned count,limit;uint8_t data[4096];};
static struct queue *allocated[8];static unsigned allocated_count;
QueueHandle_t xQueueCreate(unsigned n,size_t size){struct queue*q=calloc(1,sizeof(*q));q->size=size;q->limit=n;assert(n*size<=sizeof(q->data));allocated[allocated_count++]=q;return q;}
BaseType_t xQueueSend(QueueHandle_t q,const void*p,unsigned wait){if(q->count==q->limit)return 0;memcpy(q->data+q->count++*q->size,p,q->size);return 1;}
BaseType_t xQueueSendFromISR(QueueHandle_t q,const void*p,BaseType_t*w){return xQueueSend(q,p,0);}
BaseType_t xQueueReceive(QueueHandle_t q,void*p,unsigned wait){if(!q->count)return 0;memcpy(p,q->data,q->size);memmove(q->data,q->data+q->size,--q->count*q->size);return 1;}
void xQueueReset(QueueHandle_t q){q->count=0;}
BaseType_t xTaskCreate(void(*f)(void*),const char*n,unsigned s,void*a,unsigned p,void*h){return 1;}
static int last_poll_reason,last_poll_detail;
static int scenario,now_ms,tx_num,final_stage,final_count,rate_changes,driver_state;
static bool link_up,busy,stop_flag,timer_active;
static twai_event_callbacks_t driver_callbacks;
static const twai_frame_t *inflight;
static uint8_t snapshot[8];static uint32_t snapshot_id;static size_t snapshot_len;
static uint8_t last_data[8];static int last_tx_ms;
static uint32_t completed_tx_bits;
static bool receive_ready;
static twai_frame_header_t receive_header;
static bridge_packet_t emitted_load[8];
static unsigned emitted_load_count, raw_tx_events,raw_tx_success_events,raw_tx_failure_events,raw_tx_arbitration_events,raw_query_events,tx_uart_logs;
static bridge_packet_t last_query_tx;
static bridge_packet_t query_acks[16];
static unsigned query_ack_count;
static void enqueue_query(unsigned type,unsigned id,unsigned hz,unsigned token){bridge_packet_t p={.bytes={1,type}};wire_put_u32(p.bytes+4,id);wire_put_u32(p.bytes+8,hz);wire_put_u32(p.bytes+16,token);assert(xQueueSend(allocated[3],&p,0));}
static jmp_buf done;
int64_t esp_timer_get_time(void){return (int64_t)now_ms*1000;}
const char *esp_err_to_name(int e){return "simulation";}
void vTaskDelay(unsigned ms){
 now_ms+=ms;
 if(inflight){assert(inflight->header.id==snapshot_id);assert(inflight->buffer_len==snapshot_len);assert(!memcmp(inflight->buffer,snapshot,snapshot_len));if(!(scenario==16&&now_ms<400)){bool failed=((scenario==4||scenario==9)&&tx_num==2)||scenario==10||scenario==11||(scenario==12&&tx_num<4);
 if(failed&&driver_callbacks.on_error){twai_error_event_data_t error={0};if(scenario==11||scenario==12)error.err_flags.arb_lost=true;else error.err_flags.ack_err=true;driver_callbacks.on_error((void*)1,&error,NULL);}
 if(!failed)completed_tx_bits+=snapshot_len==8?111:79;
 twai_tx_done_event_data_t e={.is_tx_success=!failed};inflight=NULL;driver_callbacks.on_tx_done((void*)1,&e,NULL);}}
 if(now_ms==100&&(scenario==2||scenario==8))link_up=false;
 if(now_ms==100&&scenario==3)stop_flag=true;
 if(scenario==21){
  if(now_ms>=100&&now_ms<300){
   simulated_uart=true;
   receive_header=(twai_frame_header_t){.id=0x201,.dlc=8};receive_ready=true;driver_callbacks.on_rx_done((void*)1,NULL,NULL);
  }
  if(now_ms==100)enqueue_query(0x14,0x123,1000,0x511);
  if(now_ms>=300)simulated_uart=false;
 }
 if((scenario==13||scenario==14)&&now_ms%10==0){receive_header=(twai_frame_header_t){.id=0x201,.dlc=8};receive_ready=true;driver_callbacks.on_rx_done((void*)1,NULL,NULL);}
 if(scenario==14&&now_ms==400){bridge_packet_t rate={.bytes={1,0x12}};wire_put_u32(rate.bytes+4,500000);xQueueSend(allocated[2],&rate,0);}
 if(scenario==16){
  if(now_ms==100)enqueue_query(0x14,0x123,1000,0x112);
  if(now_ms==200)enqueue_query(0x14,0x456,100,0x113);
  if(now_ms==300)enqueue_query(0x15,0,0,0x114);
 }
 if(scenario==17){
  if(now_ms==50)driver_state=TWAI_ERROR_BUS_OFF;
  if(now_ms==100)enqueue_query(0x14,0x123,1000,0x201);
  if(now_ms==200)enqueue_query(0x14,0x123,0,0x202);
  if(now_ms==300)enqueue_query(0x15,0,0,0x203);
  if(now_ms==400)driver_state=TWAI_ERROR_ACTIVE;
  if(now_ms==500)enqueue_query(0x14,0x123,1000,0x204);
 }
 if(now_ms>=(scenario>=13?2300:1000))longjmp(done,1);
}
TaskHandle_t xTaskGetCurrentTaskHandle(void){return (void*)1;}
void xTaskNotifyGive(TaskHandle_t t){}
void vTaskNotifyGiveFromISR(TaskHandle_t t,BaseType_t*w){}
unsigned ulTaskNotifyTake(BaseType_t clear,unsigned ms){vTaskDelay(timer_active?1:ms);return 0;}
int esp_timer_create(const esp_timer_create_args_t *a,esp_timer_handle_t *t){*t=(void*)1;return 0;}
int esp_timer_start_periodic(esp_timer_handle_t t,uint64_t us){assert(us==250);timer_active=true;return 0;}
int esp_timer_stop(esp_timer_handle_t t){timer_active=false;return 0;}
int twai_new_node_onchip(const twai_onchip_node_config_t*c,twai_node_handle_t*n){
#if CONFIG_IDF_TARGET_ESP32S3
 assert(c->io_cfg.tx==4&&c->io_cfg.rx==5);
#else
 assert(c->io_cfg.tx==21&&c->io_cfg.rx==22);
#endif
 assert(c->bit_timing.bitrate==1000000);*n=(void*)1;return 0;}

int twai_node_register_event_callbacks(twai_node_handle_t n,const twai_event_callbacks_t*c,void*x){driver_callbacks=*c;return 0;}
int twai_node_enable(twai_node_handle_t n){driver_state=0;return 0;}
int twai_node_disable(twai_node_handle_t n){driver_state=3;return 0;}
int twai_node_reconfig_timing(twai_node_handle_t n,const twai_timing_advanced_config_t*t,const void*f){assert(driver_state==3);assert(t->brp==(scenario==14?8:4)&&t->tseg_1==15&&t->tseg_2==4);rate_changes++;return 0;}
int twai_node_get_info(twai_node_handle_t n,twai_node_status_t*s,void*x){memset(s,0,sizeof(*s));s->state=driver_state;return 0;}
int twai_node_recover(twai_node_handle_t n){if(scenario!=17)driver_state=0;return 0;}
int twai_node_receive_from_isr(twai_node_handle_t n,twai_frame_t*f){if(!receive_ready)return 2;receive_ready=false;f->header=receive_header;return 0;}
int twai_node_transmit(twai_node_handle_t n,const twai_frame_t*f,int wait){assert(!inflight);assert(f->buffer_len<=8);snapshot_id=f->header.id;snapshot_len=f->buffer_len;memcpy(snapshot,f->buffer,f->buffer_len);memset(last_data,0,8);memcpy(last_data,f->buffer,f->buffer_len);tx_num++;last_tx_ms=now_ms;if(scenario==19)return ESP_ERR_INVALID_STATE;inflight=f;return 0;}
void ble_bridge_init(QueueHandle_t q,QueueHandle_t queries){
 if(scenario==0||scenario==13||scenario==14||scenario==17||scenario==20)return;
 if(scenario>=7){bridge_packet_t c={.bytes={1,0x14}};wire_put_u32(c.bytes+4,scenario==19?0x456:0x123);wire_put_u32(c.bytes+8,1000);if(scenario==16)wire_put_u32(c.bytes+16,0x111);if(scenario==19)wire_put_u32(c.bytes+16,0x411);if(scenario==18){wire_put_u32(c.bytes+16,0x311);stop_flag=true;}xQueueSend(queries,&c,0);return;}
 bridge_packet_t p={.bytes={1,0x20,0x40,8}};wire_put_u32(p.bytes+4,0x200);p.bytes[9]=100;wire_put_u32(p.bytes+16,42);
 if(scenario==2){p.bytes[2]=0x80;wire_put_u32(p.bytes+4,0x201);p.bytes[3]=4;}
 if(scenario==5){p.bytes[1]=0x12;wire_put_u32(p.bytes+4,1000000);}
 xQueueSend(q,&p,0);busy=scenario!=5;
 if(scenario==6)stop_flag=true;
}
static unsigned rate_events;
void ble_bridge_publish(const bridge_packet_t*p){
 if(p->bytes[1]==14){rate_events++;assert(p->bytes[2]==0&&p->bytes[3]==0);assert(wire_u32(p->bytes+12)>=1000000);assert(wire_u32(p->bytes+16)==500000||wire_u32(p->bytes+16)==1000000);}
 if(p->bytes[1]==13){assert(emitted_load_count<8);emitted_load[emitted_load_count++]=*p;}
 if(p->bytes[1]==2||p->bytes[1]==3){
  raw_tx_events++;if(p->bytes[1]==2)raw_tx_success_events++;else raw_tx_failure_events++;
  if(p->bytes[1]==3&&(p->bytes[2]&0x80))raw_tx_arbitration_events++;
  if(wire_u32(p->bytes+4)==0x7ff){
   uint16_t id=scenario==19?0x456:0x123;
   assert(p->bytes[3]==4&&(p->bytes[2]&3)==0&&wire_u32(p->bytes+16)==0);
   if(p->bytes[1]==2)assert(p->bytes[2]==0);
   assert(!memcmp(p->bytes+8,(uint8_t[]){id&255,id>>8,0xcc,0},4));
   assert(wire_u32(p->bytes+12)==0);last_query_tx=*p;raw_query_events++;
  }
 }
 if(p->bytes[1]==8){last_poll_reason=p->bytes[2];last_poll_detail=p->bytes[3];if(wire_u32(p->bytes+16)){assert(query_ack_count<16);query_acks[query_ack_count++]=*p;}}
 if(p->bytes[1]==6&&p->bytes[2]!=1){final_stage=p->bytes[2];final_count++;assert(wire_u32(p->bytes+16)==42);}
}
uint32_t ble_bridge_dropped(void){return 0;}
bool ble_bridge_connected(void){return link_up;}
bool ble_bridge_take_stop(void){bool v=stop_flag;stop_flag=false;return v;}
void ble_bridge_set_motor_busy(bool value){busy=value;}
#include "../firmware/main/feedback_poll.c"
#include "../firmware/main/can_rates.c"
#include "../firmware/main/can_load.c"
#undef ESP_LOGI
#define ESP_LOGI(tag,format,...) test_log(format)
static void test_log(const char *format){if(!strncmp(format,"TX ",3))tx_uart_logs++;}
#include "../firmware/main/can_demo.c"
int main(void){
 for(scenario=0;scenario<=12;scenario++){
  last_poll_reason=last_poll_detail=0;now_ms=tx_num=final_stage=final_count=rate_changes=driver_state=0;link_up=true;timer_active=false;busy=false;stop_flag=false;inflight=NULL;tx_count=tx_failed=tx_arbitration=0;completed_tx_bits=0;
  raw_tx_events=raw_tx_success_events=raw_tx_failure_events=raw_tx_arbitration_events=raw_query_events=tx_uart_logs=query_ack_count=emitted_load_count=0;
  if(setjmp(done)==0)app_main();
  if(scenario==0||scenario==5||scenario==6)assert(tx_num==0);
  if(scenario==1){assert(tx_num>=40&&tx_num<=51);assert(last_tx_ms<=510);assert(final_stage==2);assert(!memcmp(last_data,(uint8_t[8]){0},8));}
  if(scenario==2){assert(last_tx_ms<=110);assert(final_stage==3);assert(!memcmp(last_data,(uint8_t[]){255,255,255,255,255,255,255,253},8));}
  if(scenario==3){assert(last_tx_ms<=110);assert(final_stage==3);}
  if(scenario==4){assert(tx_num==3&&final_stage==4);}
  if(scenario>=1&&scenario<=4)assert(final_count==1&&!busy);
  if(scenario==5)assert(rate_changes==1);
  if(scenario==6)assert(!busy);
  if(scenario==7){assert(tx_num==1000);assert(snapshot_id==0x7ff&&snapshot_len==4);assert(!memcmp(last_data,(uint8_t[]){0x23,1,0xcc,0},4));}
  if(scenario==8)assert(tx_num==100&&last_tx_ms<100);
  if(scenario==9)assert(tx_num>900&&timer_active&&tx_failed==1);
  if(scenario==10)assert(tx_num==5&&!timer_active&&last_poll_reason==4&&last_poll_detail==POLL_ERR_ACK);
  if(scenario==11)assert(tx_num==32&&tx_arbitration==32&&tx_failed==0&&!timer_active&&last_poll_reason==4&&last_poll_detail==POLL_ERR_ARB);
  if(scenario==12)assert(tx_num>900&&timer_active&&tx_failed==0&&tx_arbitration==3);
  assert(raw_tx_success_events==tx_count&&raw_tx_failure_events==tx_failed+tx_arbitration&&raw_tx_arbitration_events==tx_arbitration);
  assert(raw_tx_events==tx_count+tx_failed+tx_arbitration);
  if(scenario>=7){assert(raw_query_events==raw_tx_events&&tx_uart_logs==0);}
  else assert(tx_uart_logs==raw_tx_events);
  if(scenario==10)assert(raw_tx_failure_events==5&&last_query_tx.bytes[1]==3&&last_query_tx.bytes[2]==4);
  if(scenario==11)assert(raw_tx_failure_events==32&&raw_tx_arbitration_events==32);
  bridge_packet_t load_packet;
  assert(can_load_snapshot(&bus_load,&load_packet,1000000));
  assert(wire_u32(load_packet.bytes+8)==completed_tx_bits);
  assert(wire_u32(load_packet.bytes+4)==0&&wire_u32(load_packet.bytes+12)==1000000);
  if(scenario==7)assert(wire_u32(load_packet.bytes+8)==79000&&(load_packet.bytes[2]|load_packet.bytes[3]<<8)==790);
 for(unsigned i=0;i<allocated_count;i++)free(allocated[i]);allocated_count=0;
 }
 // Management commands must not wait for a hardware TX completion or recovery.
 for(scenario=16;scenario<=19;scenario++){
  now_ms=tx_num=rate_changes=driver_state=0;link_up=true;timer_active=false;busy=false;stop_flag=false;inflight=NULL;receive_ready=false;
  completed_tx_bits=emitted_load_count=raw_tx_events=query_ack_count=0;
  tx_count=tx_failed=tx_arbitration=raw_tx_success_events=raw_tx_failure_events=raw_tx_arbitration_events=raw_query_events=tx_uart_logs=0;
  if(setjmp(done)==0)app_main();
  if(scenario==16){
   assert(query_ack_count==4&&tx_num==1&&!timer_active);
   for(unsigned i=0;i<4;i++)assert(wire_u32(query_acks[i].bytes+16)==0x111+i);
   assert(query_acks[0].bytes[2]==0&&query_acks[1].bytes[2]==0&&query_acks[2].bytes[2]==0&&query_acks[3].bytes[2]==1);
  }else if(scenario==17){
   assert(query_ack_count==4&&tx_num>1000);
   for(unsigned i=0;i<4;i++)assert(wire_u32(query_acks[i].bytes+16)==0x201+i);
   assert(query_acks[0].bytes[2]==4&&query_acks[0].bytes[3]==POLL_ERR_BUS_OFF);
   assert(query_acks[1].bytes[2]==1&&query_acks[2].bytes[2]==1&&query_acks[3].bytes[2]==0);
  }else if(scenario==18)assert(query_ack_count==1&&wire_u32(query_acks[0].bytes+16)==0x311&&query_acks[0].bytes[2]==1&&tx_num==0);
  else{assert(query_ack_count==1&&wire_u32(query_acks[0].bytes+16)==0x411&&query_acks[0].bytes[2]==0);assert(tx_num==5&&tx_failed==5&&tx_count==0&&!timer_active&&last_poll_reason==4&&last_poll_detail==POLL_ERR_DRIVER);assert(raw_query_events==5&&raw_tx_failure_events==5&&raw_tx_success_events==0&&last_query_tx.bytes[1]==3&&last_query_tx.bytes[2]==0x40&&completed_tx_bits==0);}
  assert(raw_tx_events==tx_count+tx_failed+tx_arbitration&&raw_query_events==raw_tx_events&&tx_uart_logs==0);
  for(unsigned i=0;i<allocated_count;i++)free(allocated[i]);allocated_count=0;
 }
 tx_count=tx_failed=tx_arbitration=0;
 count_tx(false,POLL_ERR_ARB);assert(tx_arbitration==1&&tx_failed==0);
 count_tx(false,POLL_ERR_ACK|POLL_ERR_ARB);assert(tx_arbitration==1&&tx_failed==1);
 count_tx(false,0);count_tx(false,POLL_ERR_DRIVER);count_tx(false,POLL_ERR_BIT);assert(tx_failed==4);
 count_tx(true,POLL_ERR_ARB|POLL_ERR_ACK);assert(tx_count==1&&tx_failed==4&&tx_arbitration==1);
 bridge_packet_t raw={.bytes={1,0x10,0,4}};report_tx(&raw,false,POLL_ERR_ARB,true);assert(raw.bytes[1]==3&&raw.bytes[2]==0x80&&tx_arbitration==2&&tx_failed==4);
 // Type 3 reasons encode real completion flags independently of EXT/RTR.
 const uint8_t errors[]={POLL_ERR_ACK,POLL_ERR_BIT,POLL_ERR_FORM,POLL_ERR_STUFF,POLL_ERR_DRIVER,POLL_ERR_ARB|POLL_ERR_ACK,POLL_ERR_ACK|POLL_ERR_BIT|POLL_ERR_FORM|POLL_ERR_STUFF|POLL_ERR_DRIVER,0,POLL_ERR_UNKNOWN,POLL_ERR_BUS_OFF,POLL_ERR_ARB};
 const uint8_t flags[]={4,8,16,32,64,4,0x7c,0,0,0,0x80};
 for(unsigned frame_flags=0;frame_flags<4;frame_flags++)for(unsigned i=0;i<sizeof(errors);i++){
  bridge_packet_t p={.bytes={1,0x10,0xfc|frame_flags,8}};wire_put_u32(p.bytes+4,0x12345);wire_put_u32(p.bytes+16,0xabc);memcpy(p.bytes+8,(uint8_t[]){1,2,3,4,5,6,7,8},8);
  bridge_packet_t before=p;unsigned failures=tx_failed,arbitration=tx_arbitration,successes=tx_count;
  report_tx(&p,false,errors[i],false);
  assert(p.bytes[1]==3&&p.bytes[2]==(frame_flags|flags[i]));
  assert(!memcmp(p.bytes+3,before.bytes+3,17)); // ID/DLC/data/token unchanged.
  assert(tx_failed==failures+(errors[i]!=POLL_ERR_ARB)&&tx_arbitration==arbitration+(errors[i]==POLL_ERR_ARB)&&tx_count==successes);
  report_tx(&p,true,errors[i],false);assert(p.bytes[1]==2&&p.bytes[2]==frame_flags&&tx_count==successes+1);
 }
 // RX estimates are counted at the ISR even when its software queue is full.
 rx_queue=xQueueCreate(1,sizeof(rx_message_t));can_load_reset(&bus_load,1000000,0);
 atomic_store(&rx_count,0);atomic_store(&rx_dropped,0);
 receive_header=(twai_frame_header_t){.id=0x201,.dlc=8};receive_ready=true;on_rx(NULL,NULL,NULL);
 receive_header.ide=true;receive_ready=true;on_rx(NULL,NULL,NULL);
 receive_header.ide=false;receive_header.rtr=true;receive_ready=true;on_rx(NULL,NULL,NULL);
 assert(atomic_load(&rx_count)==3&&atomic_load(&rx_dropped)==2);
 on_rx(NULL,NULL,NULL);assert(atomic_load(&rx_count)==3); // Unread/failed RX is excluded.
 bridge_packet_t load_packet;assert(can_load_snapshot(&bus_load,&load_packet,1000000));
 assert(wire_u32(load_packet.bytes+4)==289&&wire_u32(load_packet.bytes+8)==0);
 // Successful completion consumes an in-flight estimate once; failure consumes it without counting.
 can_load_reset(&bus_load,1000000,1000000);xQueueReset(tx_done_queue=xQueueCreate(1,sizeof(tx_result_t)));
 atomic_store(&inflight_frame_bits,131);twai_tx_done_event_data_t completion={.is_tx_success=true};
 on_tx(NULL,&completion,NULL);on_tx(NULL,&completion,NULL);
 atomic_store(&inflight_frame_bits,111);completion.is_tx_success=false;on_tx(NULL,&completion,NULL);
 assert(can_load_snapshot(&bus_load,&load_packet,2000000)&&wire_u32(load_packet.bytes+8)==131);
 for(unsigned i=0;i<allocated_count;i++)free(allocated[i]);allocated_count=0;
 // Actual owner-loop emission: RX stays complete through queue loss, rate
 // change starts a new denominator; publishing query TX never counts it twice.
 for(scenario=13;scenario<=15;scenario++){
  now_ms=tx_num=rate_changes=driver_state=0;link_up=true;timer_active=false;busy=false;stop_flag=false;inflight=NULL;receive_ready=false;
  completed_tx_bits=emitted_load_count=raw_tx_events=0;
  tx_count=tx_failed=tx_arbitration=raw_tx_success_events=raw_tx_failure_events=raw_tx_arbitration_events=raw_query_events=tx_uart_logs=0;
  atomic_store(&rx_count,0);atomic_store(&rx_dropped,0);
  if(setjmp(done)==0)app_main();
  assert(emitted_load_count==(scenario==14?1:2));
  for(unsigned i=0;i<emitted_load_count;i++){
   bridge_packet_t *p=&emitted_load[i];assert(wire_u32(p->bytes+12)==1000000);
   if(scenario==15){assert(wire_u32(p->bytes+4)==0&&wire_u32(p->bytes+8)==79000&&(p->bytes[2]|p->bytes[3]<<8)==790);}
   else{assert(wire_u32(p->bytes+4)==11100&&wire_u32(p->bytes+8)==0&&(p->bytes[2]|p->bytes[3]<<8)==(scenario==14?222:111));}
   assert(wire_u32(p->bytes+16)==(scenario==14?500000:1000000));
  }
  if(scenario==15)assert(raw_tx_events==tx_count&&raw_tx_events==2299&&raw_query_events==raw_tx_events&&tx_num==2300&&tx_uart_logs==0);
  else assert(atomic_load(&rx_dropped)>0&&atomic_load(&rx_count)==230);
  if(scenario==14)assert(rate_changes==1);
  for(unsigned i=0;i<allocated_count;i++)free(allocated[i]);allocated_count=0;
 }
 // Receive peer frames while BLE is disconnected; keep the same CAN owner and TX buffer lifetime.
 scenario=20;simulated_dual=true;dual_taken=0;link_up=false;timer_active=false;busy=false;stop_flag=false;inflight=NULL;receive_ready=false;
 now_ms=tx_num=rate_changes=driver_state=0;completed_tx_bits=emitted_load_count=raw_tx_events=query_ack_count=0;
 tx_count=tx_failed=tx_arbitration=raw_tx_success_events=raw_tx_failure_events=raw_tx_arbitration_events=raw_query_events=tx_uart_logs=0;
 if(setjmp(done)==0)app_main();
 assert(dual_taken==3&&tx_num==3&&tx_count==3&&tx_failed==0&&tx_uart_logs==0&&!timer_active);
 assert(snapshot_id==0x323&&snapshot_len==8);
 simulated_dual=false;for(unsigned i=0;i<allocated_count;i++)free(allocated[i]);allocated_count=0;
 puts("PASS: dual CAN forwarding uses the real owner loop with App disconnected, preserving frame lifetime and completion accounting.");
 // Switching an active CAN query to UART stops TX/RX and cancels queued requests.
 scenario=21;simulated_uart=false;link_up=true;timer_active=false;busy=false;stop_flag=false;inflight=NULL;receive_ready=false;
 now_ms=tx_num=rate_changes=driver_state=0;completed_tx_bits=emitted_load_count=raw_tx_events=query_ack_count=0;
 tx_count=tx_failed=tx_arbitration=raw_tx_success_events=raw_tx_failure_events=raw_tx_arbitration_events=raw_query_events=tx_uart_logs=0;
 atomic_store(&rx_count,0);atomic_store(&rx_dropped,0);
 if(setjmp(done)==0)app_main();
 assert(tx_num==100&&last_tx_ms<100&&!timer_active&&atomic_load(&rx_count)==0);
 assert(query_ack_count==1&&wire_u32(query_acks[0].bytes+16)==0x511&&query_acks[0].bytes[2]==1);
 for(unsigned i=0;i<allocated_count;i++)free(allocated[i]);allocated_count=0;
 puts("PASS: UART selection stops active CAN queries, rejects queued enables, drains CAN ISR without forwarding and never resumes queries automatically.");
 assert(rate_events>0);
 puts("PASS: actual firmware owner loop: boot silence, bounded pulse, disconnect stop, explicit stop, TX failure abort, bitrate timing, queued pulse cancellation, TX buffer lifetime, 1000 Hz polling payload/pacing, polling disconnect and TX failure; device-timed rate publication.");
 puts("PASS: CAN load owner integration: all successful TX origins, no duplicate/failed TX, queue-full RX, periodic wire events, rate-change window and real query TX telemetry without UART flood.");
 puts("PASS: query management while TX held, original-token ACKs, stop before late TX, recovery rejection/stop/resume and queued enable cancellation.");
 puts("PASS: query TX success, ACK/arbitration failures and driver rejection payloads, exactly-once counters and unchanged load estimates.");
 puts("PASS: type3 real ACK/BIT/FORM/STUFF/DRIVER reasons, mixed errors, unknown/unavailable, pure arbitration and EXT/RTR preservation; success clears stale reason bits.");
}
