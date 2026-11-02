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
static unsigned resets,commands_received,command_status;
static uint8_t sent[32768];static size_t sent_count;
int64_t esp_timer_get_time(void){return (int64_t)now*1000;}
bool network_usb_mode(void){return selected;}
bool usb_serial_jtag_is_connected(void){return physical;}
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t*c){assert(c->tx_buffer_size==8192&&c->rx_buffer_size==4096);return 0;}
int usb_serial_jtag_write_bytes(const void*p,size_t n,uint32_t w){(void)w;assert(sent_count+n<=sizeof(sent));memcpy(sent+sent_count,p,n);sent_count+=n;return short_write?3:(int)n;}
int usb_serial_jtag_read_bytes(void*p,uint32_t n,uint32_t w){(void)p;(void)n;(void)w;return 0;}
void ble_bridge_reset_session(void){resets++;}
int ble_bridge_command(const uint8_t p[20]){assert(p[0]==1);commands_received++;return command_status;}
bool bridge_packet_is_raw(const bridge_packet_t*p){return p->bytes[1]==1||p->bytes[1]==19;}
static void feed(uint32_t id,const uint8_t*p){uint8_t frame[32];usb_encode(frame,id,p);service(frame,13);service(frame+13,19);}
static void hello(uint32_t id){uint8_t p[20]={1,USB_HELLO};memcpy(p+4,"CANUSB1",7);feed(id,p);}
int main(void){
 usb_link_init();assert(!usb_link_connected());
 uint8_t command[20]={1,0x19,1},ping[20]={1,USB_PING},bye[20]={1,USB_BYE};
 feed(10,command);assert(!commands_received);
 selected=false;hello(10);assert(!usb_link_connected()&&!sent_count);selected=true;
 hello(10);assert(usb_link_connected()&&resets==1&&sent_count==32&&sent[10]==1);
 hello(10);assert(resets==1); // Duplicate HELLO cannot clear active commands.
 feed(11,command);assert(!commands_received);
 feed(10,command);assert(commands_received==1&&sent[sent_count-23]==18);
 command_status=17;feed(10,command);assert(sent[sent_count-22]==17);
 bridge_packet_t raw={.bytes={1,19}},control={.bytes={1,21}};
 for(unsigned i=0;i<128;i++)assert(usb_link_send(&raw));
 assert(!usb_link_send(&raw));assert(usb_link_send(&control));
 size_t before=sent_count;service(NULL,0);assert(sent[before+9]==21&&sent_count==before+32*32);
 hello(11);assert(session_id==11&&raw_out->count==0&&resets==2);
 feed(10,command);assert(commands_received==2);
 now+=3999;feed(11,ping);assert(usb_link_connected());
 now+=4001;service(NULL,0);assert(!usb_link_connected()&&resets==3);
 feed(11,command);assert(commands_received==2);
 hello(12);physical=false;service(NULL,0);assert(!usb_link_connected());physical=true;
 hello(13);feed(13,bye);assert(!usb_link_connected());
 hello(14);short_write=true;feed(14,ping);assert(!usb_link_connected());short_write=false;
 hello(15);usb_link_reset();assert(!usb_link_connected());feed(15,command);assert(commands_received==2);
 free(raw_out->data);free(raw_out);free(control_out->data);free(control_out);
 puts("USB firmware: handshake, partial input, session isolation, priority/backpressure, heartbeat, unplug, close and short write PASS");
}
