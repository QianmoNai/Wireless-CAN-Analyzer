#include "motor_sim.h"
#include <string.h>
#include <math.h>
#include <float.h>

_Static_assert(SIM_DJI_FIRST_ID >= 1 && SIM_DJI_COUNT >= 1 &&
 SIM_DJI_FIRST_ID + SIM_DJI_COUNT <= (SIM_PROFILE == SIM_DJI_GM6020 ? 8 : 9), "invalid DJI IDs");
_Static_assert(SIM_DM_ID >= 1 && SIM_DM_ID <= 15, "DM ID must be 1..15");
_Static_assert(SIM_DM_MASTER_ID <= 0x7FF && SIM_DM_MASTER_ID != SIM_DM_CONTROL_ID, "DM ID collision");
_Static_assert(SIM_DJI_FEEDBACK_MS > 0 && SIM_DM_REPLY_QUEUE > 0, "invalid schedule");
_Static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24, "requires IEEE754 binary32");
_Static_assert(SIM_BROADCAST_PERIOD_MS > 0, "broadcast period must be positive");
#define TWO_PI 6.28318530718f

static float limit(float x, float lo, float hi) { return x < lo ? lo : x > hi ? hi : x; }
static int16_t signed_be(const uint8_t *p)
{
  int32_t u = ((uint32_t)p[0] << 8) | p[1];
  return (int16_t)(u >= 32768 ? u - 65536 : u);
}
static void put_be(uint8_t *p, uint16_t n) { p[0] = n >> 8; p[1] = n & 255; }
static float decode(unsigned n, float lo, float hi, unsigned bits)
{ return lo + (hi-lo) * (float)n / (float)((1U << bits)-1); }
static unsigned encode(float x, float lo, float hi, unsigned bits)
{ return (unsigned)((limit(x,lo,hi)-lo) * (float)((1U << bits)-1) / (hi-lo)); }

/* Cubic forward/back trajectory: continuous position and velocity.
 * Returns normalized position [-1,1] and its derivative per second. */
static void broadcast_wave(uint32_t phase, unsigned stream, float *position, float *velocity)
{
  uint32_t shifted = (phase + stream * 700U) % SIM_BROADCAST_CYCLE_MS;
  uint32_t half = SIM_BROADCAST_CYCLE_MS / 2;
  float x = (float)(shifted % half) / (float)half;
  float sign = shifted < half ? 1.0f : -1.0f;
  *position = sign * (2.0f*x*x*(3.0f-2.0f*x)-1.0f);
  *velocity = sign * 12.0f*x*(1.0f-x) / ((float)half/1000.0f);
}

static void broadcast_frame(const MotorSim *s, unsigned stream, CAN_Frame *f)
{
  float p, v;
  broadcast_wave(s->broadcast_phase, stream, &p, &v);
  *f = (CAN_Frame){.length=8};
  if (stream < 3) {
    static const uint32_t ids[] = {0x201, 0x202, 0x205};
    /* +/- half-turn travel, derivative expressed in RPM. */
    unsigned angle = (unsigned)((p+1.0f)*4095.5f);
    int16_t rpm = (int16_t)(v*30.0f);
    int16_t current = (int16_t)(p*(stream == 1 ? 3000.0f : 6000.0f));
    f->id = ids[stream];
    put_be(f->data, (uint16_t)angle);
    put_be(f->data+2, (uint16_t)rpm);
    put_be(f->data+4, (uint16_t)current);
    f->data[6] = stream == 1 ? 0 : (uint8_t)(35.0f+5.0f*p);
  } else {
    f->id = 0x11U + stream-3;
    f->data[0] = 0x10U | (stream-2); /* enabled; motor IDs 1..4 */
    unsigned qp = encode(p*SIM_DM_P_MAX*0.5f, -SIM_DM_P_MAX, SIM_DM_P_MAX, 16);
    unsigned qv = encode(v*SIM_DM_P_MAX*0.5f, -SIM_DM_V_MAX, SIM_DM_V_MAX, 12);
    unsigned qt = encode(p*SIM_DM_T_MAX*0.4f, -SIM_DM_T_MAX, SIM_DM_T_MAX, 12);
    put_be(f->data+1, (uint16_t)qp);
    f->data[3] = qv >> 4;
    f->data[4] = ((qv & 15) << 4) | (qt >> 8);
    f->data[5] = qt & 255;
    f->data[6] = (uint8_t)(35.0f+5.0f*p);
    f->data[7] = (uint8_t)(40.0f+5.0f*p);
  }
}

void MotorSim_Init(MotorSim *s, uint32_t now)
{
  memset(s, 0, sizeof(*s));
  s->tick = now;
  s->last_feedback = now;
  s->prefer_dm = 1;
  s->dm.torque_ratio = 1;
  s->dm.speed_limit = SIM_DM_V_MAX;
  if (SIM_IS_BROADCAST) s->broadcast_pending = 0x07U;
}

void MotorSim_Update(MotorSim *s, uint32_t now)
{
  uint32_t elapsed = now - s->tick;
  if (SIM_IS_BROADCAST) {
    s->broadcast_phase = (s->broadcast_phase + elapsed % SIM_BROADCAST_CYCLE_MS) % SIM_BROADCAST_CYCLE_MS;
    s->tick = now;
    if ((uint32_t)(now-s->last_feedback) >= SIM_BROADCAST_PERIOD_MS) {
      s->last_feedback = now;
      s->broadcast_pending = 0x07U; /* Coalesce to latest samples, bounded backlog. */
    }
    return;
  }
  if (elapsed > 100) { s->skipped_ms += elapsed-100; elapsed = 100; }
  /* Bound catch-up after debugger pauses, and preserve unsigned tick wrap. */
  for (uint32_t step = elapsed; step > 0; --step) {
    uint32_t t = now - step + 1;
    for (unsigned i = 0; SIM_IS_DJI && i < SIM_DJI_COUNT; ++i) {
      SimDJI *m = &s->dji[i];
      if (m->active && (uint32_t)(t-m->last_command) >= SIM_COMMAND_TIMEOUT_MS) {
        m->active = false; m->command_raw = 0; m->current = 0; ++s->timeouts;
      }
      float target = (float)m->command_raw * (SIM_DJI_MAX_RPM / SIM_DJI_COMMAND_MAX);
      /* GM command is voltage, not measured current: synthetic load current. */
      m->current = SIM_PROFILE == SIM_DJI_GM6020 ?
        (m->active ? (int16_t)limit((target-m->rpm)/SIM_DJI_MAX_RPM*8192.0f,-16384,16384) : 0) : m->command_raw;
      m->rpm += (target-m->rpm) * (0.001f / SIM_DJI_TIME_CONSTANT);
      m->angle += m->rpm * (TWO_PI / 60.0f) * 0.001f;
      while (m->angle >= TWO_PI) m->angle -= TWO_PI;
      while (m->angle < 0) m->angle += TWO_PI;
    }
    if (SIM_IS_DM) {
      SimDM *m = &s->dm;
      if (m->status == 1 && (uint32_t)(t-m->last_command) >= SIM_COMMAND_TIMEOUT_MS) {
        m->status = 13; ++s->timeouts;
      }
      float torque;
      if (SIM_PROFILE == SIM_DM_MIT) {
        torque = m->kp*(m->p_des-m->position) + m->kd*(m->v_des-m->velocity) + m->t_ff;
      } else {
        float vtarget = SIM_PROFILE == SIM_DM_VEL ? m->v_des :
          limit(10.0f*(m->p_des-m->position), -m->speed_limit, m->speed_limit);
        torque = 2.0f*(vtarget-m->velocity);
      }
      float cap = SIM_DM_T_MAX * (SIM_PROFILE == SIM_DM_POS_FORCE ? m->torque_ratio : 1.0f);
      m->torque = m->status == 1 ? limit(torque, -cap, cap) : 0;
      m->velocity = limit(m->velocity + (m->torque-SIM_DM_DAMPING*m->velocity) /
        SIM_DM_INERTIA * 0.001f, -SIM_DM_V_MAX, SIM_DM_V_MAX);
      if (m->status == 1 && (SIM_PROFILE == SIM_DM_POS_VEL || SIM_PROFILE == SIM_DM_POS_FORCE))
        m->velocity = limit(m->velocity, -m->speed_limit, m->speed_limit);
      m->position += m->velocity * 0.001f;
      /* Synthetic travel stops keep representable position, without wrap jumps. */
      if (m->position > SIM_DM_P_MAX) { m->position = SIM_DM_P_MAX; if (m->velocity > 0) m->velocity = 0; }
      if (m->position < -SIM_DM_P_MAX) { m->position = -SIM_DM_P_MAX; if (m->velocity < 0) m->velocity = 0; }
    }
  }
  s->tick = now;
  if (SIM_IS_DJI && (uint32_t)(now-s->last_feedback) >= SIM_DJI_FEEDBACK_MS) {
    s->last_feedback = now;
    /* Coalesce delayed periodic feedback: never flood with old samples. */
    s->dji_pending = (1U << SIM_DJI_COUNT)-1;
  }
}

static void dm_reply(MotorSim *s)
{
  if (s->reply_count == SIM_DM_REPLY_QUEUE) { ++s->reply_dropped; return; }
  CAN_Frame *f = &s->replies[s->reply_head];
  *f = (CAN_Frame){.id = SIM_DM_MASTER_ID, .length = 8};
  const SimDM *m = &s->dm;
  unsigned p = encode(m->position, -SIM_DM_P_MAX, SIM_DM_P_MAX, 16);
  unsigned v = encode(m->velocity, -SIM_DM_V_MAX, SIM_DM_V_MAX, 12);
  unsigned t = encode(m->torque, -SIM_DM_T_MAX, SIM_DM_T_MAX, 12);
  f->data[0] = (m->status << 4) | SIM_DM_ID;
  put_be(&f->data[1], p);
  f->data[3] = v >> 4;
  f->data[4] = ((v & 15) << 4) | (t >> 8);
  f->data[5] = t & 255;
  f->data[6] = 30; f->data[7] = 30; /* Fixed synthetic temperatures. */
  s->reply_head = (s->reply_head+1) % SIM_DM_REPLY_QUEUE;
  ++s->reply_count;
}

static uint16_t u16_le(const uint8_t *b) { return (uint16_t)b[0] | ((uint16_t)b[1]<<8); }
static void broadcast_reply(MotorSim *s, unsigned id)
{
  if (s->reply_count == SIM_DM_REPLY_QUEUE) { ++s->reply_dropped; return; }
  broadcast_frame(s, id+2, &s->replies[s->reply_head]);
  s->reply_head = (s->reply_head+1) % SIM_DM_REPLY_QUEUE;
  ++s->reply_count;
}

static bool float_le(const uint8_t *b, float *value)
{
  uint32_t bits = (uint32_t)b[0] | ((uint32_t)b[1]<<8) | ((uint32_t)b[2]<<16) | ((uint32_t)b[3]<<24);
  memcpy(value, &bits, 4);
  return isfinite(*value);
}

bool MotorSim_OnFrame(MotorSim *s, const CAN_Frame *f, uint32_t now)
{
  MotorSim_Update(s, now);
  if (f->id == 0x7FF) {
    /* DM status read: node ID little-endian, CC 00; SDKs use 4 bytes
     * or 8 bytes with zero padding. This is independent of control mode. */
    bool valid = !f->extended && (f->length == 4 || f->length == 8) &&
      f->data[2] == 0xCC && f->data[3] == 0;
    if (f->length == 8)
      for (unsigned i = 4; i < 8; ++i) valid = valid && f->data[i] == 0;
    unsigned id = u16_le(f->data);
    if (!valid || !(SIM_IS_BROADCAST ? (id >= 1 && id <= 4) :
                    (SIM_IS_DM && id == SIM_DM_ID))) {
      ++s->rejected;
      return false;
    }
    if (SIM_IS_BROADCAST) broadcast_reply(s, id);
    else dm_reply(s);
    /* Read-only: do not enable, change targets or refresh control timeout. */
    return true;
  }
  if (SIM_IS_BROADCAST) {
    /* DM manual: standard ID low byte selects the node, high 3 bits ignored.
     * This feedback-only profile accepts any data DLC 0..8, no actuation. */
    unsigned id = f->id & 0xFFU;
    if (f->extended || f->id > 0x7FF || f->length > 8 || id < 1 || id > 4) {
      ++s->rejected;
      return false;
    }
    broadcast_reply(s, id);
    return true;
  }
  if (f->extended || f->length > 8) { ++s->rejected; return false; }
  if (SIM_IS_DJI && f->length == 8 && (f->id == SIM_DJI_GROUP1 || f->id == SIM_DJI_GROUP2)) {
    bool matched = false;
    for (unsigned i = 0; SIM_IS_DJI && i < SIM_DJI_COUNT; ++i) {
      unsigned id = SIM_DJI_FIRST_ID+i;
      if (f->id != (id <= 4 ? SIM_DJI_GROUP1 : SIM_DJI_GROUP2)) continue;
      SimDJI *m = &s->dji[i];
      m->command_raw = (int16_t)limit(signed_be(&f->data[((id-1)%4)*2]), -SIM_DJI_COMMAND_MAX, SIM_DJI_COMMAND_MAX);
      if (SIM_PROFILE != SIM_DJI_GM6020) m->current = m->command_raw;
      m->last_command = now; m->active = true; ++m->commands;
      matched = true;
    }
    if (!matched) ++s->rejected;
    return matched;
  }
  if (!SIM_IS_DM || f->id != SIM_DM_CONTROL_ID) { ++s->rejected; return false; }
  SimDM *m = &s->dm;
  bool special = f->length == 8;
  for (unsigned i = 0; i < 7; ++i) special = special && f->data[i] == 255;
  uint8_t cmd = f->data[7];
  if (special && (cmd == 0xFC || cmd == 0xFD || cmd == 0xFE || cmd == 0xFB)) {
    if (cmd == 0xFC && m->status == 0) {
      m->status = 1; m->kp = m->kd = m->t_ff = m->v_des = 0;
      m->p_des = m->position; m->speed_limit = 0; m->torque_ratio = 1;
    } else if (cmd == 0xFD) {
      if (m->status == 1) m->status = 0;
      m->torque = 0;
    } else if (cmd == 0xFE) {
      m->position = 0; m->p_des = 0;
    } else if (cmd == 0xFB) {
      m->status = 0; m->torque = 0;
    }
  } else if (SIM_PROFILE == SIM_DM_MIT && f->length == 8) {
    m->p_des = decode(((unsigned)f->data[0]<<8)|f->data[1], -SIM_DM_P_MAX, SIM_DM_P_MAX, 16);
    m->v_des = decode(((unsigned)f->data[2]<<4)|(f->data[3]>>4), -SIM_DM_V_MAX, SIM_DM_V_MAX, 12);
    m->kp = decode(((unsigned)(f->data[3]&15)<<8)|f->data[4], 0, 500, 12);
    m->kd = decode(((unsigned)f->data[5]<<4)|(f->data[6]>>4), 0, 5, 12);
    m->t_ff = decode(((unsigned)(f->data[6]&15)<<8)|f->data[7], -SIM_DM_T_MAX, SIM_DM_T_MAX, 12);
  } else if (SIM_PROFILE != SIM_DM_MIT &&
             f->length == (SIM_PROFILE == SIM_DM_VEL ? 4 : 8)) {
    float p = 0, v = 0, ratio = 1;
    if (SIM_PROFILE == SIM_DM_VEL) {
      if (!float_le(f->data, &v)) { ++s->rejected; return false; }
    } else {
      if (!float_le(f->data, &p)) { ++s->rejected; return false; }
      if (SIM_PROFILE == SIM_DM_POS_VEL) {
        if (!float_le(f->data+4, &v) || v < 0) { ++s->rejected; return false; }
      } else {
        v = limit(u16_le(f->data+4), 0, 10000) / 100.0f;
        ratio = limit(u16_le(f->data+6), 0, 10000) / 10000.0f;
      }
    }
    m->p_des = limit(p, -SIM_DM_P_MAX, SIM_DM_P_MAX);
    m->v_des = limit(v, -SIM_DM_V_MAX, SIM_DM_V_MAX);
    m->speed_limit = limit(v, 0, SIM_DM_V_MAX);
    m->torque_ratio = ratio;
  } else { ++s->rejected; return false; }
  m->last_command = now; ++m->commands;
  dm_reply(s);
  return true;
}

bool MotorSim_PeekFeedback(MotorSim *s, CAN_Frame *f)
{
  s->selected_kind = 0;
  if (SIM_IS_BROADCAST) {
    if (s->reply_count && (s->prefer_dm || !s->broadcast_pending)) {
      *f = s->replies[s->reply_tail];
      s->selected_kind = 1;
      return true;
    }
    for (unsigned j=0; j<3; ++j) {
      unsigned i = (s->broadcast_cursor+j)%3;
      if (!(s->broadcast_pending & (1U<<i))) continue;
      broadcast_frame(s, i, f);
      s->selected_kind = 3;
      s->selected_index = i;
      return true;
    }
    return false;
  }
  if (s->reply_count && (s->prefer_dm || !s->dji_pending)) {
    *f = s->replies[s->reply_tail]; s->selected_kind = 1; return true;
  }
  for (unsigned j = 0; j < SIM_DJI_COUNT; ++j) {
    unsigned i = (s->dji_cursor+j) % SIM_DJI_COUNT;
    if (!(s->dji_pending & (1U<<i))) continue;
    const SimDJI *m = &s->dji[i];
    *f = (CAN_Frame){.id = SIM_DJI_FEEDBACK_BASE+SIM_DJI_FIRST_ID+i, .length = 8};
    unsigned angle = (unsigned)(m->angle*(8192.0f/TWO_PI));
    put_be(&f->data[0], (uint16_t)(angle & 8191));
    put_be(&f->data[2], (uint16_t)(int16_t)m->rpm);
    put_be(&f->data[4], (uint16_t)m->current);
    f->data[6] = SIM_PROFILE == SIM_DJI_C610 ? 0 : 30;
    s->selected_kind = 2; s->selected_index = i;
    return true;
  }
  return false;
}

void MotorSim_CommitFeedback(MotorSim *s)
{
  if (s->selected_kind == 3) {
    s->broadcast_pending &= ~(1U<<s->selected_index);
    s->broadcast_cursor = (s->selected_index+1)%3;
    s->prefer_dm = 1;
  } else if (s->selected_kind == 1) {
    s->reply_tail = (s->reply_tail+1) % SIM_DM_REPLY_QUEUE;
    --s->reply_count; s->prefer_dm = 0;
  } else if (s->selected_kind == 2) {
    s->dji_pending &= ~(1U << s->selected_index);
    s->dji_cursor = (s->selected_index+1) % SIM_DJI_COUNT;
    s->prefer_dm = 1;
  }
  s->selected_kind = 0;
}
