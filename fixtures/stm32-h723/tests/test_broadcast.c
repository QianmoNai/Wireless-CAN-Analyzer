#include "motor_sim.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
static const unsigned ids[7]={0x201,0x202,0x205,0x11,0x12,0x13,0x14};
static int16_t signed_be(const uint8_t *p) {
  unsigned v=(p[0]<<8)|p[1]; return (int16_t)(v<32768 ? (int)v : (int)v-65536);
}
static unsigned get_stream(unsigned id) {
  for(unsigned i=0;i<7;++i) if(ids[i]==id)return i;
  assert(0);return 0;
}
int main(void) {
  MotorSim s;MotorSim_Init(&s,0);
  int minv[7],maxv[7],mini[7],maxi[7];unsigned changed[7]={0},counts[7]={0};
  CAN_Frame previous[7]={0},f,again;
  for(unsigned i=0;i<7;++i){minv[i]=mini[i]=INT_MAX;maxv[i]=maxi[i]=INT_MIN;}
  for(uint32_t now=0;now<8000;now+=10) {
    MotorSim_Update(&s,now);unsigned seen=0;
    /* No DM frames are created by time alone. */
    assert(s.reply_count==0 && s.broadcast_pending==7);
    for(unsigned id=1;id<=4;++id) {
      CAN_Frame query={.id=(id-1)*0x100+id,.length=(id==3 ? 4 : 8)};
      assert(MotorSim_OnFrame(&s,&query,now));
    }
    while(MotorSim_PeekFeedback(&s,&f)) {
      assert(MotorSim_PeekFeedback(&s,&again));assert(!memcmp(&f,&again,sizeof(f)));
      unsigned i=get_stream(f.id);assert(!(seen&(1U<<i)));seen|=1U<<i;
      assert(!f.extended && f.length==8);
      int v,current;
      if(i<3){
        assert(((f.data[0]<<8)|f.data[1])<=8191);assert(f.data[7]==0);
        if(i==1)assert(f.data[6]==0);else assert(f.data[6]>=30 && f.data[6]<=40);
        v=signed_be(f.data+2);current=signed_be(f.data+4);
      }else{
        assert(f.data[0]==(0x10U|(i-2)));
        v=((f.data[3]<<4)|(f.data[4]>>4))-2048;
        current=(((f.data[4]&15)<<8)|f.data[5])-2048;
        assert(f.data[6]>=30 && f.data[6]<=40 && f.data[7]>=35 && f.data[7]<=45);
      }
      if(v<minv[i])minv[i]=v;
      if(v>maxv[i])maxv[i]=v;
      if(current<mini[i])mini[i]=current;
      if(current>maxi[i])maxi[i]=current;
      if(counts[i] && memcmp(previous[i].data,f.data,8))++changed[i];
      previous[i]=f;++counts[i];MotorSim_CommitFeedback(&s);
    }
    assert(seen==0x7F);
    MotorSim_Update(&s,now+1);assert(!MotorSim_PeekFeedback(&s,&f));
  }
  for(unsigned i=0;i<7;++i){assert(counts[i]==800 && changed[i]>700);assert(minv[i]<0 && maxv[i]>0 && mini[i]<0 && maxi[i]>0);}
  /* With no queries, only three periodic DJI streams exist. */
  MotorSim_Init(&s,0);
  for(unsigned t=0;t<=1000;t+=10) {
    MotorSim_Update(&s,t);
    for(unsigned i=0;i<3;++i) {assert(MotorSim_PeekFeedback(&s,&f));assert(get_stream(f.id)<3);MotorSim_CommitFeedback(&s);}
    assert(!MotorSim_PeekFeedback(&s,&f));
  }
  /* Long backpressure coalesces DJI; only explicit queries add DM replies. */
  MotorSim_Init(&s,0);assert(MotorSim_PeekFeedback(&s,&f));MotorSim_CommitFeedback(&s);
  for(unsigned t=10;t<=10000;t+=10)MotorSim_Update(&s,t);
  assert(s.reply_count==0 && s.broadcast_pending==7);
  assert(MotorSim_PeekFeedback(&s,&f) && f.id==0x202);
  for(unsigned i=0;i<3;++i){assert(MotorSim_PeekFeedback(&s,&f));MotorSim_CommitFeedback(&s);}
  assert(!MotorSim_PeekFeedback(&s,&f));
  /* ID low-byte matching, every high-bit combination, all classical DLCs. */
  for(unsigned high=0;high<8;++high) for(unsigned id=1;id<=4;++id) for(unsigned dlc=0;dlc<=8;++dlc) {
    f=(CAN_Frame){.id=(high<<8)|id,.length=dlc};
    assert(MotorSim_OnFrame(&s,&f,10000));
    assert(MotorSim_PeekFeedback(&s,&again) && again.id==0x10+id);
    MotorSim_CommitFeedback(&s);assert(!MotorSim_PeekFeedback(&s,&again));
  }
  /* Frame snapshots stay in request order even as simulation advances. */
  for(unsigned id=1;id<=4;++id) {f=(CAN_Frame){.id=id,.length=0};assert(MotorSim_OnFrame(&s,&f,10000));}
  assert(MotorSim_PeekFeedback(&s,&previous[0]));
  MotorSim_Update(&s,10001);assert(MotorSim_PeekFeedback(&s,&f));assert(!memcmp(&f,&previous[0],sizeof(f)));
  for(unsigned id=1;id<=4;++id) {assert(MotorSim_PeekFeedback(&s,&f) && f.id==0x10+id);MotorSim_CommitFeedback(&s);}
  unsigned bad_ids[]={0,5,0x11,0x12,0x13,0x14,0x205,0x7FF,0x801};
  for(unsigned i=0;i<sizeof(bad_ids)/sizeof(bad_ids[0]);++i) {
    f=(CAN_Frame){.id=bad_ids[i],.length=8};assert(!MotorSim_OnFrame(&s,&f,10001));
  }
  f=(CAN_Frame){.id=1,.length=8,.extended=1};assert(!MotorSim_OnFrame(&s,&f,10001));
  f.extended=0;f.length=9;assert(!MotorSim_OnFrame(&s,&f,10001));
  assert(s.reply_count==0);
  /* Queue full is bounded, counted; requests cannot starve DJI. */
  MotorSim_Init(&s,0);f=(CAN_Frame){.id=1,.length=0};
  for(unsigned i=0;i<SIM_DM_REPLY_QUEUE+2;++i) assert(MotorSim_OnFrame(&s,&f,0));
  assert(s.reply_count==SIM_DM_REPLY_QUEUE && s.reply_dropped==2);
  unsigned dm_count=0,dji_count=0;
  while(MotorSim_PeekFeedback(&s,&f)) {
    if(get_stream(f.id)<3) ++dji_count;else ++dm_count;
    if(dm_count==2)assert(dji_count>=1);
    MotorSim_CommitFeedback(&s);
  }
  assert(dm_count==SIM_DM_REPLY_QUEUE && dji_count==3);
  /* Tick wrap preserves waveform phase. */
  MotorSim_Init(&s,UINT32_MAX-4);MotorSim_Update(&s,5);assert(s.broadcast_phase==10);
  puts("PASS mixed feedback: DJI periodic, DM query-only, changing data, low-byte IDs, queue/retry/fairness/tick wrap");
}
