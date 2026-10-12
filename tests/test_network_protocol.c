#include "network_protocol.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static network_assembly_t a;
static network_credentials_t out;
static bool feed(uint8_t op,uint8_t txn,uint8_t offset,uint8_t n,const char *data,uint32_t time){uint8_t p[20]={op,txn,offset,n};if(data)memcpy(p+4,data,n);return network_assembly_packet(&a,p,time,&out);}
int main(void){
 assert(feed(NET_BEGIN,9,4,8,NULL,0));
 assert(feed(NET_DATA,9,0,12,"testpassword",10));
 assert(feed(NET_APPLY,9,0,0,NULL,20));assert(!a.active&&out.ssid_len==4&&out.password_len==8&&!memcmp(out.password,"password",8));
 assert(!feed(NET_APPLY,9,0,0,NULL,21)); // Cannot replay committed credentials.
 assert(feed(NET_BEGIN,1,4,8,NULL,0));assert(!feed(NET_DATA,2,0,4,"test",10));assert(!a.active);
 assert(feed(NET_BEGIN,1,4,8,NULL,0));assert(!feed(NET_DATA,1,1,4,"test",10));assert(!a.active);
 assert(feed(NET_BEGIN,1,4,8,NULL,0));assert(feed(NET_DATA,1,0,4,"test",10));assert(!feed(NET_DATA,1,0,4,"test",20)); // Reordered/duplicate fragment.
 assert(feed(NET_BEGIN,1,4,8,NULL,0));assert(!feed(NET_APPLY,1,0,0,NULL,20));
 assert(feed(NET_BEGIN,1,4,8,NULL,0));assert(!feed(NET_DATA,1,0,4,"test",10001));
 assert(feed(NET_BEGIN,1,4,0,NULL,UINT32_MAX-5));assert(feed(NET_DATA,1,0,4,"test",2));assert(feed(NET_APPLY,1,0,0,NULL,3)); // Clock wrap and open AP.
 assert(!feed(NET_BEGIN,1,33,8,NULL,0));assert(!feed(NET_BEGIN,1,0,0,NULL,0));
 out=(network_credentials_t){.ssid_len=32,.password_len=64};memset(out.ssid,'x',32);memset(out.password,'a',64);assert(network_credentials_valid(&out));
 out.password[63]='g';assert(!network_credentials_valid(&out));out.password_len=63;assert(network_credentials_valid(&out));out.password[0]=0;assert(!network_credentials_valid(&out));
 out.password_len=7;assert(!network_credentials_valid(&out));out.password_len=0;out.ssid[31]=0;assert(!network_credentials_valid(&out));
 assert(feed(NET_BEGIN,4,4,8,NULL,0));assert(feed(NET_DATA,4,0,12,"testpassword",10));network_assembly_reset(&a);
 network_assembly_t zero={0};assert(!memcmp(&a,&zero,sizeof(a)));
 puts("Wi-Fi credentials: bounds, fragmentation, replay, timeout, rollover and secret clearing PASS");
}
