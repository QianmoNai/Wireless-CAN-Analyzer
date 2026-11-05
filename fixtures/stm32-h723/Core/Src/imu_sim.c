#include "imu_sim.h"
#include <math.h>
#include <string.h>

_Static_assert(SIM_IMU_CAN_ID <= 0x7FF && SIM_IMU_MASTER_ID <= 0x7FF &&
  SIM_IMU_CAN_ID != SIM_IMU_MASTER_ID && SIM_IMU_CAN_ID != 0x6FF &&
  SIM_IMU_CAN_ID != 0x7FF, "invalid IMU IDs");
_Static_assert(SIM_IMU_PERIOD_MS >= 1 && SIM_IMU_PERIOD_MS <= 60000, "invalid IMU period");
_Static_assert(!SIM_IMU_ENABLED || !SIM_IS_BROADCAST ||
  ((SIM_IMU_CAN_ID & 255) > 4 && (SIM_IMU_MASTER_ID & 255) > 4 &&
   SIM_IMU_CAN_ID != 0x205 && SIM_IMU_MASTER_ID != 0x205 &&
   (SIM_IMU_CAN_ID < 0x11 || SIM_IMU_CAN_ID > 0x14) &&
   (SIM_IMU_MASTER_ID < 0x11 || SIM_IMU_MASTER_ID > 0x14)), "IMU/mixed motor ID collision");
_Static_assert(!SIM_IMU_ENABLED || !SIM_IS_DM ||
  (SIM_IMU_CAN_ID != SIM_DM_CONTROL_ID && SIM_IMU_CAN_ID != SIM_DM_MASTER_ID &&
   SIM_IMU_MASTER_ID != SIM_DM_CONTROL_ID && SIM_IMU_MASTER_ID != SIM_DM_MASTER_ID), "IMU/DM ID collision");
_Static_assert(!SIM_IMU_ENABLED || !SIM_IS_DJI ||
  (SIM_IMU_CAN_ID != SIM_DJI_GROUP1 && SIM_IMU_CAN_ID != SIM_DJI_GROUP2 &&
   SIM_IMU_MASTER_ID != SIM_DJI_GROUP1 && SIM_IMU_MASTER_ID != SIM_DJI_GROUP2 &&
   (SIM_IMU_CAN_ID < SIM_DJI_FEEDBACK_BASE+SIM_DJI_FIRST_ID ||
    SIM_IMU_CAN_ID >= SIM_DJI_FEEDBACK_BASE+SIM_DJI_FIRST_ID+SIM_DJI_COUNT) &&
   (SIM_IMU_MASTER_ID < SIM_DJI_FEEDBACK_BASE+SIM_DJI_FIRST_ID ||
    SIM_IMU_MASTER_ID >= SIM_DJI_FEEDBACK_BASE+SIM_DJI_FIRST_ID+SIM_DJI_COUNT)), "IMU/DJI ID collision");

static unsigned encode(float x, float bound, unsigned bits)
{
  if (x < -bound) x = -bound;
  if (x > bound) x = bound;
  return (unsigned)((x+bound)*((1U<<bits)-1)/(2*bound));
}
static void put16(uint8_t *p, unsigned x) { p[0]=x; p[1]=x>>8; }

/* One coherent sample: oscillating ZYX orientation, body angular velocity,
 * gravity in body axes. No translational acceleration or thermal model. */
static void sample(const ImuSim *s, CAN_Frame frames[4])
{
  const float pi=3.14159265358979323846f, frequency=2*pi/8;
  float t=s->phase*(2*pi/8000);
  float roll=0.35f*sinf(t), pitch=0.25f*sinf(2*t), yaw=0.70f*sinf(t);
  float rd=0.35f*frequency*cosf(t), pd=0.50f*frequency*cosf(2*t);
  float yd=0.70f*frequency*cosf(t);
  float sr=sinf(roll), cr=cosf(roll), sp=sinf(pitch), cp=cosf(pitch);
  float values[3][3]={
    {-9.8f*sp, 9.8f*cp*sr, 9.8f*cp*cr},
    {rd-yd*sp, pd*cr+yd*sr*cp, -pd*sr+yd*cr*cp},
    {pitch*180/pi, yaw*180/pi, roll*180/pi}
  };
  for (unsigned i=0;i<4;++i)
    frames[i]=(CAN_Frame){.id=SIM_IMU_MASTER_ID,.length=8,.data={i+1,0}};
  /* Temperature byte is zero: no simulated temperature measurement. */
  for (unsigned i=0;i<3;++i) for (unsigned axis=0;axis<3;++axis) {
    float bound=i==0 ? 235.2f : i==1 ? 34.88f : axis==0 ? 90.0f : 180.0f;
    put16(frames[i].data+2+2*axis, encode(values[i][axis],bound,16));
  }
  sr=sinf(roll/2); cr=cosf(roll/2); sp=sinf(pitch/2); cp=cosf(pitch/2);
  float sy=sinf(yaw/2), cy=cosf(yaw/2);
  unsigned w=encode(cr*cp*cy+sr*sp*sy,1,14);
  unsigned x=encode(sr*cp*cy-cr*sp*sy,1,14);
  unsigned y=encode(cr*sp*cy+sr*cp*sy,1,14);
  unsigned z=encode(cr*cp*sy-sr*sp*cy,1,14);
  uint8_t *b=frames[3].data;
  /* Manual's 56-bit layout; example decoder's W mask has a typo. */
  b[1]=w>>6; b[2]=((w&63)<<2)|(x>>12); b[3]=x>>4;
  b[4]=((x&15)<<4)|(y>>10); b[5]=y>>2; b[6]=((y&3)<<6)|(z>>8); b[7]=z;
}

void ImuSim_Init(ImuSim *s, uint32_t now)
{
  memset(s,0,sizeof(*s));
  s->tick=s->last_sample=now; s->period_ms=SIM_IMU_PERIOD_MS;
  s->active=SIM_IMU_ACTIVE; s->prefer_reply=true;
}
void ImuSim_Update(ImuSim *s, uint32_t now)
{
  s->phase=(s->phase+(now-s->tick)%8000)%8000; s->tick=now;
  if (SIM_IMU_ENABLED && s->active && now-s->last_sample>=s->period_ms) {
    s->last_sample=now; sample(s,s->periodic); s->pending=15;
  }
}
static void enqueue(ImuSim *s, const CAN_Frame *f)
{
  if (s->count==SIM_IMU_QUEUE_SIZE) { ++s->dropped; return; }
  s->replies[s->head]=*f; s->head=(s->head+1)%SIM_IMU_QUEUE_SIZE; ++s->count;
}
bool ImuSim_OnFrame(ImuSim *s, const CAN_Frame *f, uint32_t now)
{
  ImuSim_Update(s,now);
  if (!SIM_IMU_ENABLED || f->extended) goto rejected;
  unsigned reg, action;
  uint32_t value=0;
  if (f->id==0x6FF && f->length==4 && f->data[3]==0xCC &&
      ((unsigned)f->data[0]|((unsigned)f->data[1]<<8))==SIM_IMU_CAN_ID) {
    /* Compatibility with the V1.2 manual appendix's request example. */
    reg=f->data[2]; action=0;
    if (reg<1 || reg>4) goto rejected;
  } else if (f->id==SIM_IMU_CAN_ID && f->length==8 &&
             f->data[0]==0xCC && f->data[3]==0xDD && f->data[2]<=1) {
    reg=f->data[1]; action=f->data[2];
    for (unsigned i=0;i<4;++i) value|=(uint32_t)f->data[i+4]<<(8*i);
  } else goto rejected;
  ++s->requests;
  if (reg>=1 && reg<=4 && action==0) {
    CAN_Frame frames[4]; sample(s,frames); enqueue(s,&frames[reg-1]); return true;
  }
  CAN_Frame ack={.id=SIM_IMU_MASTER_ID,.length=8,.data={0xCC,reg,0xDD,0}};
  if (reg==0x0A || reg==0x0B) {
    if (action && ((reg==0x0A && (value<1 || value>60000)) || (reg==0x0B && value>1)))
      ack.data[3]=2;
    else {
      if (action) {
        if (reg==0x0A) s->period_ms=value;
        else s->active=value;
        s->pending=0; s->last_sample=now;
      }
      value=reg==0x0A ? s->period_ms : (uint32_t)s->active;
    }
  } else if (!action && (reg==0x0C || reg==0x0D || reg==0x0E)) {
    value=reg==0x0C ? 0 : reg==0x0D ? SIM_IMU_CAN_ID : SIM_IMU_MASTER_ID;
  } else {
    /* Known but unimplemented commands fail; never claim to save/calibrate. */
    ack.data[3]=(reg<=0x0F || reg==0xFE || reg==0xFF) ? 3 : 1;
    value=0;
  }
  for (unsigned i=0;i<4;++i) ack.data[i+4]=value>>(8*i);
  enqueue(s,&ack); return true;
rejected:
  ++s->rejected; return false;
}
bool ImuSim_PeekFeedback(ImuSim *s, CAN_Frame *f)
{
  s->selected=0;
  if (!SIM_IMU_ENABLED) return false;
  if (s->count && (s->prefer_reply || !s->pending)) {
    *f=s->replies[s->tail]; s->selected=1; return true;
  }
  for (unsigned j=0;j<4;++j) {
    unsigned i=(s->cursor+j)%4;
    if (s->pending&(1U<<i)) {
      *f=s->periodic[i]; s->selected=2; s->selected_index=i; return true;
    }
  }
  return false;
}
void ImuSim_CommitFeedback(ImuSim *s)
{
  if (s->selected==1) {
    s->tail=(s->tail+1)%SIM_IMU_QUEUE_SIZE; --s->count; s->prefer_reply=false;
  } else if (s->selected==2) {
    s->pending&=~(1U<<s->selected_index); s->cursor=(s->selected_index+1)%4;
    s->prefer_reply=true;
  }
  s->selected=0;
}
