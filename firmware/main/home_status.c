#include "home_status.h"
#include <string.h>
bool home_command(home_status_t *s,const bridge_packet_t *p,bridge_packet_t *ack) {
 const uint8_t *b=p->bytes;uint32_t revision=wire_u32(b+16);
 if(b[0]!=1||!revision||wire_u32(b+12))return false;
 if(b[1]==0x16){
  uint32_t id=wire_u32(b+4),feedback=wire_u32(b+8);
  if(b[2]>=HOME_MOTORS||b[3]>2||!id||id>(b[3]?0x7fe:8)||feedback>0x7ff)return false;
  if(revision!=s->staged_revision){memset(s->staged,0,sizeof(s->staged));s->staged_mask=0;s->staged_revision=revision;}
  s->staged[b[2]]=(home_motor_t){.id=id,.feedback=feedback,.family=b[3]};
  s->staged_mask|=UINT32_C(1)<<b[2];return true;
 }
 if(b[1]!=0x17||b[2]>3||b[3]>HOME_MOTORS||(b[2]==2?wire_u32(b+8)>1000:wire_u32(b+8)!=0)||(b[2]==2?wire_u32(b+4)>=b[3]:wire_u32(b+4)!=0))return false;
 uint32_t mask=b[3]==32?UINT32_MAX:((UINT32_C(1)<<b[3])-1);
 if(b[3]&&(s->staged_revision!=revision||s->staged_mask!=mask))return false;
 // Preserve last-seen only for exactly the same matching rule.
 for(unsigned i=0;i<b[3];i++)for(unsigned j=0;j<s->count;j++){
  home_motor_t *a=&s->staged[i],*old=&s->motors[j];
  if(a->id==old->id&&a->feedback==old->feedback&&a->family==old->family){a->seen=old->seen;a->last=old->last;break;}
 }
 memcpy(s->motors,s->staged,sizeof(s->motors));s->count=b[3];s->revision=revision;s->mode=b[2];s->quiet=b[2]!=0;s->target=wire_u32(b+4);s->dirty=false;s->next_sample=0;s->sample_hz=wire_u32(b+8);s->received=s->sampled=0;s->rate_seen=false;s->sample_time=s->sample_due=0;s->stats_start=-1;memset(s->imu_due,0,sizeof(s->imu_due));
 *ack=(bridge_packet_t){.bytes={1,10,b[2],b[3]}};wire_put_u32(ack->bytes+8,s->mode==2?(s->sample_hz?s->sample_hz:20):0);wire_put_u32(ack->bytes+16,revision);return true;
}
void home_observe(home_status_t *s,const bridge_packet_t *p,int64_t now) {
 if(p->bytes[1]!=1||(p->bytes[2]&3)||p->bytes[3]<7||p->bytes[3]>8)return;
 uint32_t id=wire_u32(p->bytes+4);
 for(unsigned i=0;i<s->count;i++){
  home_motor_t *m=&s->motors[i];
  bool mode_reply=m->family==1&&((p->bytes[8]|p->bytes[9]<<8)==m->id)&&(p->bytes[10]==0x33||p->bytes[10]==0x55)&&p->bytes[11]==0x0a;
  if(m->feedback==id&&!mode_reply&&(!m->family||(m->family==2?(p->bytes[3]==8&&p->bytes[8]>=1&&p->bytes[8]<=4):(p->bytes[3]==8&&(p->bytes[8]&15)==(m->id&15))))){m->seen=true;m->last=now;if(s->mode==2&&i==s->target){s->received++;
   if(s->sample_hz){
    uint32_t stamp=wire_u32(p->bytes+16);
    if(s->rate_seen)s->sample_time+=(uint32_t)(stamp-s->rate_stamp)*UINT64_C(1000);else s->rate_seen=true;
    s->rate_stamp=stamp;
    uint64_t *due=m->family==2?&s->imu_due[p->bytes[8]-1]:&s->sample_due;
    uint64_t period=((m->family==2?4000000:1000000)+s->sample_hz-1)/s->sample_hz;
    if(s->sample_time>=*due){
     s->latest=*p;s->dirty=true;*due+=period;
     if(*due<=s->sample_time)*due=s->sample_time+period;
    }
   }else{s->latest=*p;s->dirty=true;}}}
 }
}
void home_snapshot(const home_status_t *s,bridge_packet_t *p,int64_t now) {
 *p=(bridge_packet_t){.bytes={1,9,1,s->count}};uint32_t online=0,seen=0;
 for(unsigned i=0;i<s->count;i++)if(s->motors[i].seen){seen|=UINT32_C(1)<<i;if(now-s->motors[i].last<2000)online|=UINT32_C(1)<<i;}
 wire_put_u32(p->bytes+4,online);wire_put_u32(p->bytes+8,seen);wire_put_u32(p->bytes+16,s->revision);
}

// Legacy mode coalesces at 20 Hz; explicit-rate mode emits at RX time.
bool home_detail(home_status_t *s,bridge_packet_t *p,int64_t now) {
 if(s->mode!=2||!s->dirty||(!s->sample_hz&&now<s->next_sample))return false;
 *p=s->latest;p->bytes[1]=11;wire_put_u32(p->bytes+4,s->revision);
 s->dirty=false;s->next_sample=now+50;s->sampled++;return true;
}

// Cumulative selected-motor counts; independent of BLE delivery and UI rendering.
bool home_detail_stats(home_status_t *s,bridge_packet_t *p,int64_t now){
 if(s->mode!=2||!s->sample_hz)return false;
 if(s->stats_start<0){s->stats_start=now;return false;}
 *p=(bridge_packet_t){.bytes={1,12}};
 wire_put_u32(p->bytes+4,s->received);wire_put_u32(p->bytes+8,s->sampled);
 wire_put_u32(p->bytes+12,(uint32_t)(now-s->stats_start));wire_put_u32(p->bytes+16,s->revision);return true;
}
