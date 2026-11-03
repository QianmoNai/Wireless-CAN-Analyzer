// Exercise the actual firmware USB state machine without physical hardware.
#include "sim.h"
#include "../firmware/main/usb_link.c"
#include <stdio.h>
struct queue {unsigned count,limit;size_t size;uint8_t *data;};
QueueHandle_t xQueueCreate(unsigned n,size_t size){struct queue*q=calloc(1,sizeof(*q));q->limit=n;q->size=size;q->data=calloc(n,size);return q;}
int xQueueSend(QueueHandle_t q,const void*p,unsigned w){(void)w;if(q->count==q->limit)return 0;memcpy(q->data+q->size*q->count++,p,q->size);return 1;}
int xQueueReceive(QueueHandle_t q,void*p,unsigned w){(void)w;if(!q->count)return 0;memcpy(p,q->data,q->size);memmove(q->data,q->data+q->size,--q->count*q->size);return 1;}
void xQueueReset(QueueHandle_t q){q->count=0;}
SemaphoreHandle_t xSemaphoreCreateMutex(void){return (void*)1;}
int xSemaphoreTake(SemaphoreHandle_t q,unsigned w){(void)q;(void)w;return 1;}
int xSemaphoreGive(SemaphoreHandle_t q){(void)q;return 1;}
int xTaskCreate(void(*f)(void*),const char*n,unsigned s,void*a,unsigned p,void*h){(void)f;(void)n;(void)s;(void)a;(void)p;(void)h;return 1;}
void vTaskDelay(unsigned t){(void)t;}
static uint32_t now=10;static bool selected=true,physical=true,short_write=false;
static unsigned resets,commands_received,command_status,uart_dropped;
void uart_bridge_drop_packet(const bridge_packet_t *p){
 if(p->bytes[0]==1&&p->bytes[1]==19&&p->bytes[2]>=1&&p->bytes[2]<=12&&!p->bytes[3])uart_dropped+=p->bytes[2];
}
static uint8_t sent[32768];static size_t sent_count;
int64_t esp_timer_get_time(void){return (int64_t)now*1000;}
bool network_usb_mode(void){return selected;}
bool usb_serial_jtag_is_connected(void){return physical;}
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t*c){assert(c->tx_buffer_size==8192&&c->rx_buffer_size==4096);return 0;}
int usb_serial_jtag_write_bytes(const void*p,size_t n,uint32_t w){(void)w;assert(sent_count+n<=sizeof(sent));memcpy(sent+sent_count,p,n);sent_count+=n;return short_write?3:(int)n;}
int usb_serial_jtag_read_bytes(void*p,uint32_t n,uint32_t w){(void)p;(void)n;(void)w;return 0;}
void ble_bridge_reset_session(void){resets++;}
int ble_bridge_command(const uint8_t p[20]){assert(p[0]==1);commands_received++;return command_status;}
int ble_bridge_settings_command(const uint8_t p[20],bridge_packet_t *reply){
 assert(settings_command_valid(p));commands_received++;
 *reply=(bridge_packet_t){.bytes={1,SETTINGS_EVENT,p[2]?1:0}};
 wire_put_u32(reply->bytes+16,wire_u32(p+16));return command_status;
}
bool bridge_packet_is_raw(const bridge_packet_t*p){return p->bytes[1]==1||p->bytes[1]==19;}
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
 size_t before=sent_count;service(NULL,0);assert(sent[before+9]==21&&sent_count==before+32*32);
 hello(11);assert(session_id==11&&raw_out->count==0&&resets==2);
 assert(uart_dropped==65*12); // 31 + 32 raw frames delivered before split HELLO completes; 65 reset.
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
 free(raw_out->data);free(raw_out);free(control_out->data);free(control_out);
 puts("USB firmware: handshake, partial input, session isolation, priority/backpressure, heartbeat, unplug, close and short write PASS");
}
