#include <assert.h>
#include <stdio.h>
#include "../firmware/main/home_status.h"

static void row(home_status_t *s,unsigned index,unsigned family,unsigned id,unsigned feedback,unsigned rev){
 bridge_packet_t p={.bytes={1,0x16,index,family}},ack;
 wire_put_u32(p.bytes+4,id);wire_put_u32(p.bytes+8,feedback);wire_put_u32(p.bytes+16,rev);
 assert(home_command(s,&p,&ack));
}
static void commit(home_status_t *s,unsigned count,unsigned mode,unsigned rev){
 bridge_packet_t p={.bytes={1,0x17,mode,count}},ack;
 wire_put_u32(p.bytes+16,rev);assert(home_command(s,&p,&ack));
}
static bridge_packet_t feedback(unsigned id,unsigned dlc,unsigned first,unsigned mos,unsigned motor){
 bridge_packet_t p={.bytes={1,1,0,dlc}};wire_put_u32(p.bytes+4,id);
 p.bytes[8]=first;p.bytes[14]=mos;p.bytes[15]=motor;return p;
}
static unsigned fresh(const bridge_packet_t *p){return p->bytes[4]|(unsigned)p->bytes[5]<<8;}

int main(void){
 home_status_t s={0};bridge_packet_t p,rx;
 row(&s,0,0,1,0x201,1);row(&s,1,1,2,0x100,1);row(&s,2,2,0x123,0x11,1);row(&s,3,0,3,0x203,1);commit(&s,4,1,1);
 assert(home_temperature_snapshot(&s,&p,0,0)&&p.bytes[1]==16&&p.bytes[2]==0&&p.bytes[3]==4&&fresh(&p)==0&&wire_u32(p.bytes+16)==1);
 // DJI byte6 and DM byte7 differ from DM MOS byte6; zero and 255 are valid.
 rx=feedback(0x201,7,0,0,77);home_observe(&s,&rx,100);
 rx=feedback(0x100,8,0x12,61,255);home_observe(&s,&rx,200);
 rx=feedback(0x11,8,1,30,40);home_observe(&s,&rx,200);
 assert(home_temperature_snapshot(&s,&p,0,200)&&fresh(&p)==3&&p.bytes[6]==0&&p.bytes[7]==255&&p.bytes[8]==0&&p.bytes[9]==0);
 // Invalid flags/short frames, DM low-nibble mismatch and mode register replies
 // must not refresh either the online timestamp or cached motor temperature.
 rx=feedback(0x201,8,0,99,98);rx.bytes[2]=1;home_observe(&s,&rx,300);rx.bytes[2]=2;home_observe(&s,&rx,300);
 rx.bytes[2]=0;rx.bytes[3]=6;home_observe(&s,&rx,300);
 rx=feedback(0x100,8,3,99,98);home_observe(&s,&rx,300);rx.bytes[8]=2;rx.bytes[3]=7;home_observe(&s,&rx,300);
 rx=feedback(0x100,8,2,99,98);rx.bytes[10]=0x33;rx.bytes[11]=0x0a;home_observe(&s,&rx,300);
 assert(home_temperature_snapshot(&s,&p,0,2099)&&fresh(&p)==3&&p.bytes[6]==0&&p.bytes[7]==255);
 assert(home_temperature_snapshot(&s,&p,0,2100)&&fresh(&p)==2&&p.bytes[6]==0);
 assert(home_temperature_snapshot(&s,&p,0,2200)&&fresh(&p)==0&&p.bytes[7]==0);
 rx=feedback(0x201,8,0,42,99);home_observe(&s,&rx,2300);
 // Same feedback rule survives registry reordering; changed rules clear values.
 row(&s,0,1,2,0x100,2);row(&s,1,0,1,0x201,2);row(&s,2,0,3,0x204,2);commit(&s,3,1,2);
 assert(home_temperature_snapshot(&s,&p,0,2300)&&fresh(&p)==2&&p.bytes[7]==42&&wire_u32(p.bytes+16)==2);
 for(unsigned mode=0;mode<=3;mode++){
  commit(&s,3,mode,2);assert(home_temperature_snapshot(&s,&p,0,2300)==(mode==1));
 }
 // Maximum registry splits into 10+10+10+2 slots, including slot31.
 for(unsigned i=0;i<32;i++)row(&s,i,1,i+1,i+1,3);commit(&s,32,1,3);
 for(unsigned i=0;i<32;i++){rx=feedback(i+1,8,(i+1)&15,90,i);home_observe(&s,&rx,2400);}
 for(unsigned start=0;start<32;start+=10){
  assert(home_temperature_snapshot(&s,&p,start,2400)&&p.bytes[2]==start&&p.bytes[3]==32);
  unsigned length=start==30?2:10;assert(fresh(&p)==(1u<<length)-1);
  for(unsigned i=0;i<length;i++)assert(p.bytes[6+i]==start+i);
  for(unsigned i=length;i<10;i++)assert(p.bytes[6+i]==0);
 }
 assert(!home_temperature_snapshot(&s,&p,1,2400));assert(!home_temperature_snapshot(&s,&p,32,2400));
 commit(&s,0,1,4);assert(!home_temperature_snapshot(&s,&p,0,2400));
 puts("PASS: current home temperatures, DJI/DM decoding, invalid feedback exclusion, freshness, revision/reorder, quiet-only mode and 32-slot batching.");
}
