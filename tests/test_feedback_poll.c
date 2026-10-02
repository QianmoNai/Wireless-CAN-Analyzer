#include <assert.h>
#include <stdio.h>
#include "../firmware/main/feedback_poll.h"
#include "../firmware/main/ack_delivery.h"
static bridge_packet_t last;static unsigned reports;
static bool limited_queues;
static bridge_packet_t ack_queue[16],control_queue[16];
static unsigned ack_count,control_count,queue_dropped;
void ble_bridge_publish(const bridge_packet_t *b){last=*b;reports++;if(limited_queues){
 bool ack=bridge_packet_is_ack(b);unsigned *count=ack?&ack_count:&control_count;
 if(*count==16)queue_dropped++;else (ack?ack_queue:control_queue)[(*count)++]=*b;
}}
static void config(feedback_poll_t *p,unsigned id,unsigned hz,unsigned bitrate,int64_t now){bridge_packet_t b={.bytes={1,0x14}};wire_put_u32(b.bytes+4,id);wire_put_u32(b.bytes+8,hz);wire_put_u32(b.bytes+16,42);poll_command(p,&b,bitrate,now);assert(wire_u32(last.bytes+16)==42);}
int main(void){
 feedback_poll_t p={0};assert(!poll_next(&p,0));config(&p,1,100,1000000,0);assert(last.bytes[2]==0);
 assert(poll_next(&p,0)==1);assert(!poll_next(&p,9999));assert(poll_next(&p,10000)==1);
 config(&p,1,1000,1000000,0);config(&p,2,1000,1000000,0);config(&p,3,1000,1000000,0);assert(last.bytes[2]==2);config(&p,3,500,1000000,0);assert(last.bytes[2]==0);
 unsigned counts[4]={0};for(int64_t us=0;us<1000000;us+=250){unsigned id=poll_next(&p,us);if(id){counts[id]++;poll_result(&p,id,true,0,us);}}
 assert(counts[1]==1000&&counts[2]==1000&&counts[3]==500);
 poll_report(&p,1000000);assert(wire_u32(last.bytes+12)==500);
 // A late wakeup never sends a catch-up burst for one motor.
 poll_clear(&p,1);config(&p,1,1000,1000000,0);assert(poll_next(&p,15000)==1);assert(!poll_next(&p,15000));assert(poll_next(&p,16000)==1);
 // Renewals keep phase; a second motor cannot renew an abandoned motor's lease.
 config(&p,1,1000,1000000,16500);assert(!poll_next(&p,16500));assert(poll_next(&p,17000)==1);
 config(&p,2,100,1000000,1000000);poll_expire(&p,3016500);assert(p.entries[0].hz==0&&poll_active(&p));
 for(int i=0;i<5;i++)poll_result(&p,2,false,POLL_ERR_ACK,3100000+i*10000);assert(!poll_active(&p)&&last.bytes[2]==4&&last.bytes[3]==POLL_ERR_ACK);
 config(&p,1,313,125000,0);assert(last.bytes[2]==2);config(&p,1,312,125000,0);assert(last.bytes[2]==0);
 config(&p,1,0,125000,1);assert(!poll_active(&p));
 config(&p,1,1001,1000000,0);assert(last.bytes[2]==3);config(&p,0,1,1000000,0);assert(last.bytes[2]==3);
 poll_clear(&p,1);for(unsigned i=1;i<=32;i++)config(&p,i,1,1000000,0);config(&p,33,1,1000000,0);assert(last.bytes[2]==2);
 poll_expire(&p,3000000);assert(!poll_active(&p));
 // One failure remains active, obeys both configured frequency and backoff.
 poll_clear(&p,1);config(&p,1,100,1000000,0);assert(poll_next(&p,0)==1);
 poll_result(&p,1,false,POLL_ERR_ACK,100);assert(poll_active(&p)&&last.bytes[2]==0&&last.bytes[3]==POLL_ERR_ACK);
 assert(!poll_next(&p,9999));assert(poll_next(&p,10000)==1);
 poll_result(&p,1,true,0,10100);assert(p.entries[0].failures==0&&p.entries[0].detail==0);
 // Arbitration has its own higher bounded limit and cannot disable another motor.
 config(&p,2,100,1000000,11000);
 for(int i=0;i<31;i++){poll_result(&p,1,false,POLL_ERR_ARB,12000+i*1000);assert(p.entries[0].hz==100);}
 config(&p,1,100,1000000,45000);assert(p.entries[0].arbitration==31); // lease is not a reset
 poll_result(&p,1,false,POLL_ERR_ARB,46000);assert(p.entries[0].hz==0&&p.entries[1].hz==100&&last.bytes[3]==POLL_ERR_ARB);
 // Mixed arbitration+ACK is a communication error; success resets error budgets.
 for(int i=0;i<4;i++)poll_result(&p,2,false,POLL_ERR_ARB|POLL_ERR_ACK,47000+i*1000);
 assert(poll_active(&p));poll_result(&p,2,true,0,52000);
 for(int i=0;i<4;i++)poll_result(&p,2,false,POLL_ERR_BIT,53000+i*1000);
 assert(poll_active(&p));poll_result(&p,2,false,POLL_ERR_BIT,58000);assert(!poll_active(&p)&&last.bytes[2]==4);
 // Cancel/backoff/late completion never recreates a schedule; expiry wins over retry.
 config(&p,1,1000,1000000,0);assert(poll_next(&p,0)==1);poll_result(&p,1,false,0,100);
 assert(last.bytes[3]==POLL_ERR_UNKNOWN&&!poll_next(&p,1000));
 config(&p,1,0,1000000,1000);poll_result(&p,1,false,POLL_ERR_ACK,2000);assert(!poll_next(&p,100000));
 config(&p,1,100,1000000,0);poll_result(&p,1,false,POLL_ERR_DRIVER,2999999);assert(!poll_next(&p,3000000));
 config(&p,1,100,1000000,0);poll_bus_off(&p);assert(!poll_active(&p)&&last.bytes[2]==4&&last.bytes[3]==POLL_ERR_BUS_OFF);
 // Recovery rejects enable explicitly; disable and global stop still ACK.
 bridge_packet_t command={.bytes={1,0x14}};wire_put_u32(command.bytes+4,1);wire_put_u32(command.bytes+8,100);wire_put_u32(command.bytes+16,1001);
 poll_command_available(&p,&command,1000000,0,false);
 assert(!poll_active(&p)&&last.bytes[2]==4&&last.bytes[3]==POLL_ERR_BUS_OFF&&wire_u32(last.bytes+16)==1001);
 config(&p,1,100,1000000,0);wire_put_u32(command.bytes+8,0);wire_put_u32(command.bytes+16,1002);
 poll_command_available(&p,&command,1000000,0,false);
 assert(!poll_active(&p)&&last.bytes[2]==1&&wire_u32(last.bytes+16)==1002);
 // The production ACK classifier isolates stop's ACK from a 32-status burst.
 for(unsigned i=1;i<=32;i++)config(&p,i,1,1000000,0);
 limited_queues=true;ack_count=control_count=queue_dropped=0;
 command=(bridge_packet_t){.bytes={1,0x15}};wire_put_u32(command.bytes+16,1003);
 poll_command_available(&p,&command,1000000,0,false);
 assert(!poll_active(&p)&&ack_count==1&&control_count==16&&queue_dropped==16);
 assert(ack_queue[0].bytes[2]==1&&wire_u32(ack_queue[0].bytes+4)==0&&wire_u32(ack_queue[0].bytes+16)==1003);
 limited_queues=false;poll_result(&p,1,true,0,100);assert(!poll_active(&p));
 command=(bridge_packet_t){.bytes={1,0x14}};wire_put_u32(command.bytes+4,3);wire_put_u32(command.bytes+8,100);wire_put_u32(command.bytes+16,1004);
 poll_cancel_command(&command);assert(last.bytes[2]==1&&wire_u32(last.bytes+4)==3&&wire_u32(last.bytes+8)==0&&wire_u32(last.bytes+16)==1004);
 puts("PASS: 100/1000 Hz pacing, 2x1k+500Hz fairness, budget, leases, missed deadlines, TX failure, max motors and actual rate.");
 puts("PASS: recovery rejection, stop/disable ACK during recovery, isolated 32-motor stop ACK and cancelled queued requests.");
}
