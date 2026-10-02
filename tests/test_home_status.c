#include <assert.h>
#include <stdio.h>
#include "../firmware/main/home_status.h"
static bridge_packet_t ack;
static bool row(home_status_t*s,unsigned index,unsigned family,unsigned id,unsigned feedback,unsigned rev){bridge_packet_t p={.bytes={1,0x16,index,family}};wire_put_u32(p.bytes+4,id);wire_put_u32(p.bytes+8,feedback);wire_put_u32(p.bytes+16,rev);return home_command(s,&p,&ack);}
static bool commit(home_status_t*s,unsigned count,unsigned quiet,unsigned rev){bridge_packet_t p={.bytes={1,0x17,quiet,count}};wire_put_u32(p.bytes+16,rev);return home_command(s,&p,&ack);}
static void rx(home_status_t*s,unsigned id,unsigned flags,unsigned dlc,unsigned first,int64_t now){bridge_packet_t p={.bytes={1,1,flags,dlc}};wire_put_u32(p.bytes+4,id);p.bytes[8]=first;wire_put_u32(p.bytes+16,(uint32_t)now);home_observe(s,&p,now);}
int main(void){home_status_t s={0};bridge_packet_t p;
 assert(!s.quiet);assert(!commit(&s,2,1,1));assert(row(&s,0,0,1,0x201,1));assert(row(&s,1,1,2,0,1));assert(row(&s,2,1,3,0,1));assert(commit(&s,3,1,1));assert(s.quiet&&ack.bytes[1]==10&&wire_u32(ack.bytes+16)==1);
 home_snapshot(&s,&p,0);assert(wire_u32(p.bytes+4)==0&&wire_u32(p.bytes+8)==0);
 rx(&s,0x201,1,8,1,0);rx(&s,0x201,2,8,1,0);rx(&s,0x201,0,6,1,0);rx(&s,0,0,7,2,0);home_snapshot(&s,&p,0);assert(wire_u32(p.bytes+8)==0);
 rx(&s,0x201,0,7,0,100);rx(&s,0,0,8,0x12,200);home_snapshot(&s,&p,1000);assert(wire_u32(p.bytes+4)==3&&wire_u32(p.bytes+8)==3);
 home_snapshot(&s,&p,2100);assert(wire_u32(p.bytes+4)==2);home_snapshot(&s,&p,2200);assert(wire_u32(p.bytes+4)==0&&wire_u32(p.bytes+8)==3);
 assert(commit(&s,3,0,1)&&!s.quiet);assert(commit(&s,3,1,1));home_snapshot(&s,&p,1500);assert(wire_u32(p.bytes+4)==3);
 // Staging does not modify the active table. A new revision cannot inherit partial old entries.
 assert(row(&s,0,1,3,0,2));assert(!commit(&s,3,1,2));assert(s.revision==1);assert(commit(&s,1,1,2));home_snapshot(&s,&p,2200);assert(wire_u32(p.bytes+8)==0);
 rx(&s,0,0,8,3,2300);home_snapshot(&s,&p,2300);assert(wire_u32(p.bytes+4)==1);
 assert(!row(&s,32,0,1,0,3));assert(!row(&s,0,3,1,0,3));assert(!row(&s,0,1,0,0,3));assert(!row(&s,0,0,9,0,3));assert(!row(&s,0,1,1,0x800,3));
 assert(commit(&s,0,1,3));home_snapshot(&s,&p,2400);assert(!wire_u32(p.bytes+4)&&p.bytes[3]==0);
 for(unsigned i=0;i<32;i++)assert(row(&s,i,1,i+1,i+1,4));assert(commit(&s,32,1,4));rx(&s,32,0,8,0,2500);home_snapshot(&s,&p,2500);assert(wire_u32(p.bytes+4)==0x80000000u);
 // Detail selection, coalescing, no duplicate samples, stale revision and silent mode.
 assert(row(&s,0,0,1,0x201,5));assert(row(&s,1,1,2,0,5));
 bridge_packet_t cmd={.bytes={1,0x17,2,2}};wire_put_u32(cmd.bytes+4,1);wire_put_u32(cmd.bytes+16,5);assert(home_command(&s,&cmd,&ack));
 rx(&s,0x201,0,8,0,3000);rx(&s,0,0,8,3,3000);assert(!home_detail(&s,&p,3000));
 rx(&s,0,0,8,2,3001);assert(home_detail(&s,&p,3001)&&p.bytes[1]==11&&wire_u32(p.bytes+4)==5&&p.bytes[8]==2);assert(!home_detail(&s,&p,3100));
 rx(&s,0,0,8,0x12,3010);assert(!home_detail(&s,&p,3049));rx(&s,0,0,8,0x82,3040);assert(home_detail(&s,&p,3051)&&p.bytes[8]==0x82);
 wire_put_u32(cmd.bytes+4,2);assert(!home_command(&s,&cmd,&ack));cmd.bytes[3]=0;wire_put_u32(cmd.bytes+4,0);assert(!home_command(&s,&cmd,&ack));
 assert(commit(&s,0,3,6));rx(&s,0,0,8,2,3200);assert(!home_detail(&s,&p,3200));assert(s.mode==3);
 // Adjustable source-timestamp paced capture, independent of the 10ms notify task.
 for(unsigned hz=1;hz<=1000;hz=hz==1?100:hz==100?333:hz==333?1000:1001){
  unsigned rev=10+hz;assert(row(&s,0,0,1,0x201,rev));
  cmd=(bridge_packet_t){.bytes={1,0x17,2,1}};wire_put_u32(cmd.bytes+8,hz);wire_put_u32(cmd.bytes+16,rev);assert(home_command(&s,&cmd,&ack));assert(wire_u32(ack.bytes+8)==hz);
  assert(!home_detail_stats(&s,&p,0));unsigned count=0;
  for(unsigned t=0;t<1000;t++){rx(&s,0x201,0,8,t&255,t);if(home_detail(&s,&p,t)){count++;assert(wire_u32(p.bytes+16)==t);}}
  assert(count==hz);assert(s.received==1000&&s.sampled==hz);assert(!home_detail(&s,&p,2000));
  assert(home_detail_stats(&s,&p,1000)&&p.bytes[1]==12&&wire_u32(p.bytes+4)==1000&&wire_u32(p.bytes+8)==hz&&wire_u32(p.bytes+12)==1000&&wire_u32(p.bytes+16)==rev);
 }
 // Timestamp rollover must not stall sampling; unknown/invalid modes and rates rejected.
 assert(home_command(&s,&cmd,&ack));rx(&s,0x201,0,8,1,UINT32_MAX);assert(home_detail(&s,&p,0));rx(&s,0x201,0,8,2,UINT64_C(1)<<32);assert(home_detail(&s,&p,0)&&p.bytes[8]==2);
 wire_put_u32(cmd.bytes+8,1001);assert(!home_command(&s,&cmd,&ack));cmd.bytes[2]=3;wire_put_u32(cmd.bytes+8,100);assert(!home_command(&s,&cmd,&ack));
 assert(commit(&s,0,3,2000));assert(!home_detail_stats(&s,&p,4000));
 // IMU classification must not interpret type byte as motor low-nibble ID.
 assert(row(&s,0,2,0x123,0x11,3000));
 cmd=(bridge_packet_t){.bytes={1,0x17,2,1}};wire_put_u32(cmd.bytes+8,100);wire_put_u32(cmd.bytes+16,3000);assert(home_command(&s,&cmd,&ack));
 rx(&s,0x11,0,8,0xcc,0);rx(&s,0x12,0,8,1,0);rx(&s,0x11,0,7,1,0);assert(!s.motors[0].seen);
 unsigned groups[4]={0};
 for(unsigned t=0;t<1000;t++)for(unsigned kind=1;kind<=4;kind++){rx(&s,0x11,0,8,kind,t);if(home_detail(&s,&p,t))groups[p.bytes[8]-1]++;}
 for(unsigned i=0;i<4;i++)assert(groups[i]==25);assert(s.received==4000&&s.sampled==100);
 home_snapshot(&s,&p,1100);assert(wire_u32(p.bytes+4)==1);home_snapshot(&s,&p,3000);assert(wire_u32(p.bytes+4)==0);
 puts("PASS: transactional home subscriptions, bitmap bit31, per-motor feedback routing, length/flag filtering, timeout, mode switch and invalid commands.");}
