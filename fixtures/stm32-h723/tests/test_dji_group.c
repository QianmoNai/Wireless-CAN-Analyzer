#include "motor_sim.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
  MotorSim s; MotorSim_Init(&s,0);
  CAN_Frame f={.id=SIM_DJI_GROUP2,.length=8,.data={0x10,0,0xF0,0,0x7F,0xFF,0x80,0}};
  assert(MotorSim_OnFrame(&s,&f,0));
  assert(s.dji[0].command_raw==4096 && s.dji[1].command_raw==-4096);
  assert(s.dji[2].command_raw==SIM_DJI_COMMAND_MAX);
  MotorSim_Update(&s,1);
  for(unsigned i=0;i<SIM_DJI_COUNT;++i) {
    assert(MotorSim_PeekFeedback(&s,&f));assert(f.id==SIM_DJI_FEEDBACK_BASE+SIM_DJI_FIRST_ID+i);
    MotorSim_CommitFeedback(&s);
  }
  assert(!MotorSim_PeekFeedback(&s,&f));puts("PASS upper DJI group slots/IDs");
}
