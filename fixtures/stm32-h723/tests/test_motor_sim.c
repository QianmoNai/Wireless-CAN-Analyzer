#include "motor_sim.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static CAN_Frame special(uint8_t c) {
  CAN_Frame f={.id=SIM_DM_CONTROL_ID,.length=8,.data={255,255,255,255,255,255,255,0}};
  f.data[7]=c; return f;
}
static CAN_Frame pop(MotorSim *s) {
  CAN_Frame f, again;
  assert(MotorSim_PeekFeedback(s,&f));
  assert(MotorSim_PeekFeedback(s,&again));
  assert(memcmp(&f,&again,sizeof(f))==0);
  MotorSim_CommitFeedback(s); return f;
}
int main(void) {
  MotorSim s; MotorSim_Init(&s,0); CAN_Frame f;
  if (SIM_IS_DJI) {
    CAN_Frame c={.id=SIM_DJI_GROUP1,.length=8,.data={0x7F,0xFF,0,0,0,0,0,0}};
    assert(MotorSim_OnFrame(&s,&c,0)); assert(s.dji[0].command_raw==SIM_DJI_COMMAND_MAX);
    MotorSim_Update(&s,1); f=pop(&s);
    assert(f.id==SIM_DJI_FEEDBACK_BASE+1 && f.length==8 && !f.extended);
    assert(f.data[6]==(SIM_PROFILE==SIM_DJI_C610 ? 0 : 30) && f.data[7]==0);
    if(SIM_PROFILE!=SIM_DJI_GM6020) assert(((f.data[4]<<8)|f.data[5])==SIM_DJI_COMMAND_MAX);
    else assert(((f.data[4]<<8)|f.data[5])!=30000); /* voltage != current */
    for(unsigned t=2;t<100;++t) MotorSim_Update(&s,t);
    assert(s.dji[0].rpm>0);
    c.data[0]=0x80;c.data[1]=0;MotorSim_OnFrame(&s,&c,100);
    assert(s.dji[0].command_raw==-SIM_DJI_COMMAND_MAX);
    for(unsigned t=101;t<500;++t) MotorSim_Update(&s,t);
    assert(s.dji[0].rpm<0);f=pop(&s);assert(f.data[2]&0x80);
    MotorSim_Update(&s,600); assert(!s.dji[0].active && s.dji[0].current==0);
    c=special(0xFC);assert(!MotorSim_OnFrame(&s,&c,600));assert(s.dm.commands==0);
  } else {
    MotorSim_Update(&s,1);assert(!MotorSim_PeekFeedback(&s,&f)); /* no DJI background */
    CAN_Frame c={.id=0x200,.length=8};assert(!MotorSim_OnFrame(&s,&c,1));
    c=special(0xFC);assert(MotorSim_OnFrame(&s,&c,1));f=pop(&s);
    const uint8_t enabled[]={0x11,0x7F,0xFF,0x7F,0xF7,0xFF,30,30};
    assert(f.id==SIM_DM_MASTER_ID && !memcmp(f.data,enabled,8));
    c=(CAN_Frame){.id=SIM_DM_CONTROL_ID,.length=8};
    if(SIM_PROFILE==SIM_DM_MIT) {
      memset(c.data,255,8); /* P/V/T maxima, KP=500 KD=5 */
    } else if(SIM_PROFILE==SIM_DM_VEL) {
      c.length=4;c.data[2]=0x80;c.data[3]=0x3F; /* float LE +1 rad/s */
    } else {
      c.data[2]=0x80;c.data[3]=0x3F; /* +1 rad */
      if(SIM_PROFILE==SIM_DM_POS_VEL) {c.data[6]=0x80;c.data[7]=0x3F;} /* +1 rad/s */
      else {c.data[4]=100;c.data[6]=0xC4;c.data[7]=0x09;} /* 1 rad/s, 25% */
    }
    assert(MotorSim_OnFrame(&s,&c,1));pop(&s);
    if(SIM_PROFILE==SIM_DM_MIT) {assert(s.dm.kp==500 && s.dm.kd==5 && s.dm.t_ff==SIM_DM_T_MAX);}
    else if(SIM_PROFILE==SIM_DM_VEL) assert(s.dm.v_des==1);
    else {assert(s.dm.p_des==1 && s.dm.speed_limit==1);}
    if(SIM_PROFILE==SIM_DM_POS_FORCE) assert(s.dm.torque_ratio==0.25f);
    MotorSim_Update(&s,101);assert(s.dm.position>0 && s.dm.velocity>0);
    if(SIM_PROFILE==SIM_DM_POS_FORCE) assert(fabsf(s.dm.torque)<=2.5f);
    CAN_Frame bad=c; bad.length=SIM_PROFILE==SIM_DM_VEL ? 8 : 4;
    assert(!MotorSim_OnFrame(&s,&bad,101));
    if(SIM_PROFILE!=SIM_DM_MIT) {
      bad=c;bad.data[0]=0;bad.data[1]=0;bad.data[2]=0xC0;bad.data[3]=0x7F; /* NaN */
      uint32_t count=s.dm.commands;assert(!MotorSim_OnFrame(&s,&bad,101));assert(s.dm.commands==count);
      bad.data[2]=0x80;assert(!MotorSim_OnFrame(&s,&bad,101)); /* Infinity */
    }
    c=special(0xFE);MotorSim_OnFrame(&s,&c,101);f=pop(&s);assert(s.dm.position==0 && f.data[1]==0x7F && f.data[2]==0xFF);
    MotorSim_Update(&s,601);assert(s.dm.status==13 && s.dm.torque==0);
    c=special(0xFC);MotorSim_OnFrame(&s,&c,601);f=pop(&s);assert(f.data[0]==0xD1);
    c=special(0xFB);MotorSim_OnFrame(&s,&c,601);pop(&s);assert(s.dm.status==0);
    c=special(0xFC);MotorSim_OnFrame(&s,&c,601);pop(&s);assert(s.dm.status==1);
    c=special(0xFD);MotorSim_OnFrame(&s,&c,601);pop(&s);assert(s.dm.status==0);
    for(unsigned i=0;i<SIM_DM_REPLY_QUEUE+3;++i) MotorSim_OnFrame(&s,&c,601);
    assert(s.reply_count==SIM_DM_REPLY_QUEUE && s.reply_dropped==3);
    while(MotorSim_PeekFeedback(&s,&f)) MotorSim_CommitFeedback(&s);
  }
  /* Invalid frames cannot activate the unselected profile. */
  MotorSim_Init(&s,UINT32_MAX-10);MotorSim_Update(&s,20);assert(s.skipped_ms==0);
  uint32_t r=7;
  for(unsigned i=21;i<20021;++i) {
    f=(CAN_Frame){.id=SIM_IS_DJI ? SIM_DJI_GROUP1 : SIM_DM_CONTROL_ID,.length=8};
    for(unsigned j=0;j<8;++j){r=r*1664525U+1013904223U;f.data[j]=r>>24;}
    if(SIM_PROFILE==SIM_DM_VEL) f.length=4;
    if(i%11==0) f.extended=1;
    MotorSim_OnFrame(&s,&f,i);
    while(MotorSim_PeekFeedback(&s,&f)) MotorSim_CommitFeedback(&s);
    assert(isfinite(s.dm.position) && isfinite(s.dm.velocity) && isfinite(s.dji[0].rpm));
    assert(s.dm.position<=SIM_DM_P_MAX && s.dm.position>=-SIM_DM_P_MAX);
  }
  printf("PASS profile %d: %s, protocol/dynamics/isolation/timeout/bounds/fuzz\n",SIM_PROFILE,SIM_PROFILE_NAME);
}
