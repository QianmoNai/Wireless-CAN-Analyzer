// Exercise the actual firmware USB state machine without physical hardware.
#include "sim.h"
#include "../firmware/main/usb_link.c"
#include <stdio.h>
struct queue {unsigned count,limit;size_t size;uint8_t *data;};
QueueHandle_t xQueueCreate(unsigned n,size_t size){struct queue*q=calloc(1,sizeof(*q));q->limit=n;q->size=size;q->data=calloc(n,size);return q;}
int xQueueSend(QueueHandle_t q,const void*p,unsigned w){(void)w;if(q->count==q->limit)return 0;memcpy(q->data+q->size*q->count++,p,q->size);return 1;}
int xQueueReceive(QueueHandle_t q,void*p,unsigned w){(void)w;if(!q->count)return 0;memcpy(p,q->data,q->size);memmove(q->data,q->data+q->size,--q->count*q->size);return 1;}
void xQueueReset(QueueHandle_t q){q->count=0;}
unsigned uxQueueMessagesWaiting(QueueHandle_t q){return q->count;}
SemaphoreHandle_t xSemaphoreCreateMutex(void){return (void*)1;}
int xSemaphoreTake(SemaphoreHandle_t q,unsigned w){(void)q;(void)w;return 1;}
int xSemaphoreGive(SemaphoreHandle_t q){(void)q;return 1;}
int xTaskCreate(void(*f)(void*),const char*n,unsigned s,void*a,unsigned p,void*h){(void)f;(void)n;(void)s;(void)a;(void)p;*(TaskHandle_t*)h=(void*)2;return 1;}
static uint32_t now=10;static bool selected=true,physical=true,short_write=false;
static unsigned write_calls,short_write_call;
static unsigned notifications,notify_waits,delays,write_cost_us,notify_block_us;
static usj_select_notif_callback_t rx_callback;
static int64_t work_us;
static uint8_t input[256];static unsigned input_size;
void vTaskDelay(unsigned t){assert(t==1);delays++;now+=10*t;}
void xTaskNotifyGive(TaskHandle_t task){assert(task==(void*)2);notifications++;}
void vTaskNotifyGiveFromISR(TaskHandle_t task,int *woken){xTaskNotifyGive(task);*woken=pdTRUE;}
void usb_serial_jtag_set_select_notif_callback(usj_select_notif_callback_t callback){rx_callback=callback;}
unsigned ulTaskNotifyTake(int clear,unsigned ticks){
 assert(clear==pdTRUE&&ticks==1);notify_waits++;
 unsigned result=notifications;notifications=0;if(!result)now+=10*ticks;
 work_us+=notify_block_us;return result;
}
static unsigned resets,commands_received,command_status,uart_dropped;
void uart_bridge_drop_packet(const bridge_packet_t *p){
 if(p->bytes[0]==1&&p->bytes[1]==19&&p->bytes[2]>=1&&p->bytes[2]<=12&&!p->bytes[3])uart_dropped+=p->bytes[2];
}
static uint8_t sent[32768];static size_t sent_count;
int64_t esp_timer_get_time(void){return (int64_t)now*1000+work_us;}
bool network_usb_mode(void){return selected;}
bool usb_serial_jtag_is_connected(void){return physical;}
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t*c){assert(c->tx_buffer_size==8192&&c->rx_buffer_size==4096);return 0;}
int usb_serial_jtag_write_bytes(const void*p,size_t n,uint32_t w){(void)w;assert(sent_count+n<=sizeof(sent));memcpy(sent+sent_count,p,n);sent_count+=n;work_us+=write_cost_us;write_calls++;return short_write||write_calls==short_write_call?3:(int)n;}
int usb_serial_jtag_read_bytes(void*p,uint32_t n,uint32_t w){assert(w==0&&n>=input_size);unsigned size=input_size;memcpy(p,input,size);input_size=0;return (int)size;}
void ble_bridge_reset_session(void){resets++;}
int ble_bridge_command(const uint8_t p[20]){assert(p[0]==1);commands_received++;return command_status;}
int ble_bridge_settings_command(const uint8_t p[20],bridge_packet_t *reply){
 assert(settings_command_valid(p));commands_received++;
 *reply=(bridge_packet_t){.bytes={1,SETTINGS_EVENT,p[2]?1:0}};
 wire_put_u32(reply->bytes+16,wire_u32(p+16));return command_status;
}
bool bridge_packet_is_raw(const bridge_packet_t*p){return p->bytes[1]==1||p->bytes[1]==19;}
void network_display(uint8_t *state,char ip[16],uint32_t *pin){*state=2;ip[0]=0;*pin=314159;}
static unsigned local_dual_commands;
static unsigned monitor_reads;
void can_monitor_drop_counters(uint32_t counters[3]){
 monitor_reads++;counters[0]=17;counters[1]=23;counters[2]=29;
}
int dual_link_usb_command(const uint8_t p[20],uint8_t reply[20]){
 local_dual_commands++;memset(reply,0,20);reply[0]=1;reply[1]=DUAL_USB_EVENT;
 wire_put_u32(reply+16,wire_u32(p+16));return command_status;
}
static void feed(uint32_t id,const uint8_t*p){uint8_t frame[32];usb_encode(frame,id,p);service(frame,13);service(frame+13,19);}
static void hello(uint32_t id){uint8_t p[20]={1,USB_HELLO};memcpy(p+4,"CANUSB1",7);feed(id,p);}
int main(void){
 usb_link_init();assert(!usb_link_connected());
 uint8_t command[20]={1,0x18,1},ping[20]={1,USB_PING},bye[20]={1,USB_BYE};
 feed(10,command);assert(!commands_received);
 selected=false;hello(10);assert(!usb_link_connected()&&!sent_count);selected=true;
 hello(10);assert(usb_link_connected()&&resets==1&&sent_count==32&&sent[10]==1);
 hello(10);assert(resets==1); // Duplicate HELLO cannot clear active commands.
 feed(11,command);assert(!commands_received);
 feed(10,command);assert(commands_received==1&&sent[sent_count-23]==18);
 command_status=17;feed(10,command);assert(sent[sent_count-22]==17);
 bridge_packet_t raw={.bytes={1,19,12}},control={.bytes={1,21}};
 for(unsigned i=0;i<128;i++)assert(usb_link_send(&raw));
 // Caller owns immediate rejection: USB must not count it a second time.
 raw.bytes[2]=7;assert(!usb_link_send(&raw));assert(uart_dropped==0);assert(usb_link_send(&control));
 size_t before=sent_count;service(NULL,0);assert(sent[before+9]==21&&sent_count==before+64*32);
 hello(11);assert(session_id==11&&raw_out->count==0&&resets==2);
 assert(uart_dropped==12); // 63 + 64 raw frames delivered before split HELLO completes; one reset.
 assert(usb_link_send(&raw));raw.bytes[2]=3;assert(usb_link_send(&raw));
 unsigned dropped_before=uart_dropped;short_write=true;service(NULL,0);short_write=false;
 assert(!usb_link_connected()&&uart_dropped==dropped_before+7+3);
 hello(11); // Neither short-written nor cleared packets may be counted twice.
 assert(uart_dropped==dropped_before+10);
 feed(10,command);assert(commands_received==2);
 now+=3999;feed(11,ping);assert(usb_link_connected());
 unsigned reset_before=resets;now+=4001;service(NULL,0);assert(!usb_link_connected()&&resets==reset_before+1);
 feed(11,command);assert(commands_received==2);
 hello(12);physical=false;service(NULL,0);assert(!usb_link_connected());physical=true;
 hello(13);feed(13,bye);assert(!usb_link_connected());
 hello(14);short_write=true;feed(14,ping);assert(!usb_link_connected());short_write=false;
 hello(15);usb_link_reset();assert(!usb_link_connected());feed(15,command);assert(commands_received==2);
 // Active local pairing display can be read only through physical USB.
 selected=false;sent_count=0;
 uint8_t pairing_display[20]={1,0x1b};wire_put_u32(pairing_display+16,600);
 feed(601,pairing_display);
 assert(sent_count==64&&sent[41]==24&&sent[42]==1&&wire_u32(sent+44)==314159);
 pairing_display[5]=1;feed(601,pairing_display);
 assert(sent_count==96&&sent[74]==0x13);pairing_display[5]=0;
 selected=true;hello(602);before=sent_count;
 feed(603,pairing_display);assert(sent_count==before);
 feed(602,bye);
 // USB-only management works in dual mode without claiming a CAN session.
 selected=false;sent_count=0;command_status=0;
 uint8_t dual[20]={1,DUAL_USB_COMMAND};wire_put_u32(dual+16,700);
 feed(701,dual);assert(local_dual_commands==1&&sent_count==64&&!usb_link_connected());
 selected=true;hello(702);before=sent_count;
 feed(703,dual);assert(local_dual_commands==1&&sent_count==before);
 feed(702,dual);assert(local_dual_commands==2&&sent_count==before+64);
 feed(702,bye);
 // Read lifetime components outside USB mode without changing ownership,
 // settings or queues; reject malformed reads and another active session.
 selected=false;sent_count=0;unsigned monitor_resets=resets,monitor_commands=commands_received;
 uint8_t monitor[20]={1,CAN_MONITOR_USB_QUERY};wire_put_u32(monitor+16,800);
 feed(801,monitor);
 assert(monitor_reads==1&&sent_count==64&&!usb_link_connected());
 assert(sent[41]==CAN_MONITOR_USB_EVENT&&sent[42]==1&&sent[43]==0);
 assert(wire_u32(sent+44)==17&&wire_u32(sent+48)==23&&wire_u32(sent+52)==29&&wire_u32(sent+56)==800);
 for(unsigned i=2;i<16;i++){
  monitor[i]=1;before=sent_count;feed(801,monitor);
  assert(sent_count==before+32&&sent[before+10]==0x13&&monitor_reads==1);monitor[i]=0;
 }
 wire_put_u32(monitor+16,0);before=sent_count;feed(801,monitor);
 assert(sent_count==before+32&&sent[before+10]==0x13&&monitor_reads==1);
 assert(resets==monitor_resets&&commands_received==monitor_commands);
 wire_put_u32(monitor+16,800);selected=true;hello(802);before=sent_count;
 feed(803,monitor);assert(sent_count==before&&monitor_reads==1);
 assert(usb_link_send(&raw));unsigned queued=raw_out->count;monitor_resets=resets;
 assert(incoming(802,monitor));assert(monitor_reads==2&&usb_link_connected()&&raw_out->count==queued&&resets==monitor_resets);
 feed(802,bye);
 // Non-USB mode keeps the decoder across reads and only admits bootstrap.
 selected=false;sent_count=0;command_status=0;
 uint8_t query[20]={1,SETTINGS_COMMAND};wire_put_u32(query+16,123);
 feed(16,query);assert(commands_received==3&&sent_count==64&&sent[9]==18&&sent[41]==SETTINGS_EVENT&&!usb_link_connected());
 uint8_t setting[20]={1,SETTINGS_COMMAND,1,SETTINGS_DATA,0,1};wire_put_u32(setting+16,124);
 feed(16,setting);assert(commands_received==3&&sent_count==96&&sent[74]==19);
 setting[3]|=SETTINGS_NETWORK;setting[4]=4;
 feed(16,setting);assert(commands_received==4&&sent_count==160&&!usb_link_connected());
 selected=true;hello(17);assert(usb_link_connected());
 before=sent_count;feed(18,query);assert(sent_count==before&&commands_received==4);
 setting[3]=SETTINGS_DATA;setting[4]=0;feed(17,setting);assert(commands_received==5&&sent_count==before+64);
 feed(17,bye);assert(!usb_link_connected());
 // Exercise the real worker iteration: input is always nonblocking, and idle
 // waiting uses a producer notification (one tick = 10 ms on this firmware).
 hello(20);notifications=0;int64_t busy_us=0;
 unsigned waits_before=notify_waits,delays_before=delays;
 worker_step(&busy_us);assert(notify_waits==waits_before+1&&delays==delays_before);
 assert(usb_link_send(&raw)&&notifications==1);
 before=sent_count;worker_step(&busy_us);
 assert(sent_count==before+32&&notifications==0&&delays==delays_before);
 // RX interrupt uses the same wakeup; TX-ready interrupts must not cause a
 // notification storm. Deliver a PING while the worker was idle.
 int woken=0;rx_callback(USJ_SELECT_WRITE_NOTIF,&woken);assert(!woken&&!notifications);
 usb_encode(input,20,ping);input_size=32;rx_callback(USJ_SELECT_READ_NOTIF,&woken);
 assert(woken&&notifications==1);before=sent_count;worker_step(&busy_us);
 assert(sent_count==before+32&&sent[before+9]==USB_PING&&delays==delays_before);
 // A full queue takes two batches with an input/control opportunity between
 // them; neither the input wait nor a mandatory tick gates the second batch.
 for(unsigned i=0;i<128;i++)assert(usb_link_send(&raw));
 assert(usb_link_send(&control));before=sent_count;waits_before=notify_waits;
 worker_step(&busy_us);
 assert(sent[before+9]==21&&sent_count==before+64*32&&raw_out->count==65);
 assert(notify_waits==waits_before&&delays==delays_before);
 usb_encode(input,20,ping);input_size=32;assert(usb_link_send(&control));
 before=sent_count;worker_step(&busy_us);
 assert(sent[before+9]==USB_PING&&sent[before+32+9]==21&&raw_out->count==2);
 assert(notify_waits==waits_before&&delays==delays_before);
 worker_step(&busy_us);assert(!raw_out->count);
 // Slow USB writes hit the batch time budget before the frame budget.
 // Input is still checked between batches; sustained work eventually blocks
 // a real tick so even lower-priority tasks/idle cannot starve.
 for(unsigned i=0;i<20;i++)assert(usb_link_send(&raw));
 write_cost_us=500;busy_us=0;before=sent_count;
 worker_step(&busy_us);
 assert(sent_count==before+4*32&&raw_out->count==16&&delays==delays_before);
 usb_encode(input,20,ping);input_size=32;assert(usb_link_send(&control));
 before=sent_count;worker_step(&busy_us);
 assert(sent[before+9]==USB_PING&&sent[before+32+9]==21&&delays==delays_before);
 while(raw_out->count)worker_step(&busy_us);
 assert(delays==delays_before+1);
 // Repeated immediately satisfied notifications must not reset the busy
 // budget; otherwise a steady trickle can spin forever without blocking.
 usb_link_reset();hello(21);write_cost_us=600;busy_us=0;
 delays_before=delays;
 for(unsigned i=0;i<17;i++){assert(usb_link_send(&raw));worker_step(&busy_us);}
 assert(delays==delays_before+1);
 // A notification received after a genuine blocking interval has already
 // allowed other tasks to run; it must not accumulate into an extra tick.
 busy_us=0;notify_block_us=3000;write_cost_us=1200;delays_before=delays;
 for(unsigned i=0;i<4;i++){assert(usb_link_send(&raw));worker_step(&busy_us);assert(busy_us==0);}
 assert(delays==delays_before);notify_block_us=0;
 write_cost_us=0;usb_link_reset();
 // Failed physical management replies must not reset another transport's
 // shared bridge. Exercise both the ACK and the event write, all four reads,
 // fragmented input, no implicit replay, and recovery with a fresh request.
 selected=false;command_status=0;
 const uint8_t *reads[]={monitor,pairing_display,dual,query};
 for(unsigned kind=0;kind<4;kind++)for(unsigned part=1;part<=2;part++){
  sent_count=0;short_write_call=write_calls+part;reset_before=resets;
  feed(900+kind,reads[kind]);
  assert(resets==reset_before&&!atomic_load(&session_id)&&!decoder.used);
  unsigned writes_after=write_calls;service(NULL,0);assert(write_calls==writes_after);
  short_write_call=0;before=sent_count;feed(900+kind,reads[kind]);
  assert(sent_count==before+64&&resets==reset_before&&!atomic_load(&session_id));
 }
 // The same write failure inside an owned USB data session still terminates
 // that session and resets its queues/polling exactly once.
 selected=true;sent_count=0;hello(950);reset_before=resets;short_write_call=write_calls+2;
 feed(950,monitor);assert(!usb_link_connected()&&resets==reset_before+1);
 short_write_call=0;service(NULL,0);assert(resets==reset_before+1);
 free(raw_out->data);free(raw_out);free(control_out->data);free(control_out);
 puts("USB firmware: sessions, priority/backpressure, short write, worker wake, input fairness, frame/time budgets and starvation protection PASS");
}
