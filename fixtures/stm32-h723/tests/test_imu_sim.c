#include "imu_sim.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static CAN_Frame command(unsigned reg, unsigned write, uint32_t value)
{
  return (CAN_Frame){.id=SIM_IMU_CAN_ID,.length=8,
    .data={0xCC,reg,write,0xDD,value,value>>8,value>>16,value>>24}};
}
static float decode(unsigned v,float bound,unsigned bits)
{ return v*(2*bound)/((1U<<bits)-1)-bound; }
static float axis(CAN_Frame f,unsigned a,float bound)
{ return decode(f.data[2+2*a]|((unsigned)f.data[3+2*a]<<8),bound,16); }
static CAN_Frame take(ImuSim *s)
{
  CAN_Frame f,again; assert(ImuSim_PeekFeedback(s,&f));
  assert(ImuSim_PeekFeedback(s,&again) && !memcmp(&f,&again,sizeof(f)));
  assert(f.id==SIM_IMU_MASTER_ID && f.length==8 && !f.extended);
  ImuSim_CommitFeedback(s);return f;
}
static CAN_Frame transact(ImuSim *s,unsigned reg,unsigned write,uint32_t value)
{
  CAN_Frame c=command(reg,write,value);
  assert(ImuSim_OnFrame(s,&c,s->tick));return take(s);
}
int main(void)
{
  ImuSim s; ImuSim_Init(&s,0); CAN_Frame f;
  if (!SIM_IMU_ENABLED) {
    ImuSim_Update(&s,1000);assert(!ImuSim_PeekFeedback(&s,&f));
    f=command(1,0,0);assert(!ImuSim_OnFrame(&s,&f,1000));
    puts("PASS IMU disabled");return 0;
  }
  assert(!ImuSim_PeekFeedback(&s,&f));
  unsigned changes[4]={0}; CAN_Frame previous[4]={0};
  for(unsigned t=10;t<=8000;t+=10) {
    ImuSim_Update(&s,t);CAN_Frame frame[4];unsigned seen=0;
    for(unsigned i=0;i<4;++i) {
      f=take(&s);unsigned type=f.data[0]-1;
      assert(type<4 && !(seen&(1U<<type)));seen|=1U<<type;frame[type]=f;
      if(t>10 && memcmp(f.data,previous[type].data,8)) ++changes[type];
      previous[type]=f;
    }
    assert(!ImuSim_PeekFeedback(&s,&f));
    float pitch=axis(frame[2],0,90)*0.01745329252f;
    float yaw=axis(frame[2],1,180)*0.01745329252f;
    float roll=axis(frame[2],2,180)*0.01745329252f;
    /* Decode quaternion as a 56-bit bitstream, independent of encoder layout. */
    uint64_t bits=0;for(unsigned i=1;i<8;++i)bits=(bits<<8)|frame[3].data[i];
    float q[4];for(unsigned i=0;i<4;++i)q[i]=decode((bits>>(42-14*i))&0x3FFF,1,14);
    float norm=0;for(unsigned i=0;i<4;++i)norm+=q[i]*q[i];assert(fabsf(norm-1)<0.001f);
    assert(fabsf(asinf(2*(q[0]*q[2]-q[3]*q[1]))-pitch)<0.001f);
    assert(fabsf(atan2f(2*(q[0]*q[1]+q[2]*q[3]),1-2*(q[1]*q[1]+q[2]*q[2]))-roll)<0.001f);
    assert(fabsf(atan2f(2*(q[0]*q[3]+q[1]*q[2]),1-2*(q[2]*q[2]+q[3]*q[3]))-yaw)<0.001f);
    float a[3];for(unsigned i=0;i<3;++i)a[i]=axis(frame[0],i,235.2f);
    assert(fabsf(sqrtf(a[0]*a[0]+a[1]*a[1]+a[2]*a[2])-9.8f)<0.02f);
    assert(fabsf(a[0]+9.8f*sinf(pitch))<0.02f);
    assert(fabsf(a[1]-9.8f*cosf(pitch)*sinf(roll))<0.02f);
    /* Body rates must agree with Euler angle derivatives. */
    float tphase=t*0.0007853981634f;
    float rd=0.35f*0.7853981634f*cosf(tphase);
    float pd=0.50f*0.7853981634f*cosf(2*tphase);
    float yd=0.70f*0.7853981634f*cosf(tphase);
    assert(fabsf(axis(frame[1],0,34.88f)-(rd-yd*sinf(pitch)))<0.002f);
    assert(fabsf(axis(frame[1],1,34.88f)-(pd*cosf(roll)+yd*sinf(roll)*cosf(pitch)))<0.002f);
    assert(fabsf(axis(frame[1],2,34.88f)-(-pd*sinf(roll)+yd*cosf(roll)*cosf(pitch)))<0.002f);
  }
  for(unsigned i=0;i<4;++i)assert(changes[i]>700);
  f=transact(&s,0x0B,1,0);assert(f.data[3]==0 && !s.active);
  ImuSim_Update(&s,9000);assert(!ImuSim_PeekFeedback(&s,&f));
  for(unsigned type=1;type<=4;++type) {
    f=transact(&s,type,0,0);assert(f.data[0]==type);
    CAN_Frame legacy={.id=0x6FF,.length=4,.data={SIM_IMU_CAN_ID&255,SIM_IMU_CAN_ID>>8,type,0xCC}};
    assert(ImuSim_OnFrame(&s,&legacy,s.tick));CAN_Frame other=take(&s);
    assert(!memcmp(&f,&other,sizeof(f)));
    legacy.data[1]^=1;assert(!ImuSim_OnFrame(&s,&legacy,s.tick));
  }
  f=transact(&s,0x0A,1,20);assert(f.data[3]==0 && s.period_ms==20);
  f=transact(&s,0x0A,1,0);assert(f.data[3]==2 && s.period_ms==20);
  f=transact(&s,0x0B,1,2);assert(f.data[3]==2 && !s.active);
  f=transact(&s,0xFE,1,0);assert(f.data[3]==3);
  f=transact(&s,0x80,0,0);assert(f.data[3]==1);
  f=transact(&s,0x0D,0,0);assert((f.data[4]|((unsigned)f.data[5]<<8))==SIM_IMU_CAN_ID);
  f=transact(&s,0x0E,0,0);assert((f.data[4]|((unsigned)f.data[5]<<8))==SIM_IMU_MASTER_ID);
  for(unsigned dlc=0;dlc<=9;++dlc) if(dlc!=8) {
    f=command(1,0,0);f.length=dlc;assert(!ImuSim_OnFrame(&s,&f,s.tick));
  }
  f=command(1,0,0);f.extended=1;assert(!ImuSim_OnFrame(&s,&f,s.tick));
  f=command(1,0,0);f.data[3]=0;assert(!ImuSim_OnFrame(&s,&f,s.tick));
  f=command(1,0,0);f.id=0x7FF;assert(!ImuSim_OnFrame(&s,&f,s.tick));
  f=transact(&s,0x0B,1,1);assert(f.data[3]==0 && s.active);
  ImuSim_Update(&s,9019);assert(!ImuSim_PeekFeedback(&s,&f));
  ImuSim_Update(&s,9020);assert(s.pending==15);
  /* Bounded backlog, alternating replies/periodic data, retry without commit. */
  for(unsigned t=9040;t<10000;t+=20)ImuSim_Update(&s,t);
  f=command(1,0,0);
  for(unsigned i=0;i<SIM_IMU_QUEUE_SIZE+2;++i)assert(ImuSim_OnFrame(&s,&f,s.tick));
  assert(s.count==SIM_IMU_QUEUE_SIZE && s.dropped==2 && s.pending==15);
  unsigned replies=0,periodic=0;
  while(ImuSim_PeekFeedback(&s,&f)) {
    if(s.selected==1)++replies;else ++periodic;
    if(replies==2)assert(periodic>0);
    ImuSim_CommitFeedback(&s);
  }
  assert(replies==SIM_IMU_QUEUE_SIZE && periodic==4);
  ImuSim_Init(&s,UINT32_MAX-4);ImuSim_Update(&s,5);assert(s.phase==10 && s.pending==15);
  puts("PASS IMU: 100Hz four streams, quaternion/attitude/gravity/rates, queries, settings, queue, tick wrap");
}
