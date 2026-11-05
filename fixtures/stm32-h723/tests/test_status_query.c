#include "motor_sim.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static CAN_Frame query(unsigned id, unsigned length)
{
  return (CAN_Frame){.id=0x7FF, .length=length,
    .data={id & 255, id >> 8, 0xCC, 0}};
}

static void reject(MotorSim *s, CAN_Frame f)
{
  unsigned rejected = s->rejected, queued = s->reply_count;
  assert(!MotorSim_OnFrame(s, &f, s->tick));
  assert(s->rejected == rejected+1 && s->reply_count == queued);
}

/* Drain through the real scheduler, allowing periodic DJI frames. */
static CAN_Frame drain(MotorSim *s, unsigned id)
{
  CAN_Frame f, reply = {0};
  unsigned replies = 0;
  while (MotorSim_PeekFeedback(s, &f)) {
    if (s->selected_kind == 1) {
      assert(f.id == (SIM_IS_BROADCAST ? 0x10+id : SIM_DM_MASTER_ID));
      assert(!f.extended && f.length == 8 && (f.data[0] & 15) == id);
      reply = f;
      ++replies;
    }
    MotorSim_CommitFeedback(s);
  }
  assert(replies == 1);
  return reply;
}

int main(void)
{
  MotorSim s;
  MotorSim_Init(&s, 0);
  unsigned id = SIM_IS_BROADCAST ? 1 : SIM_DM_ID;
  CAN_Frame q = query(id, 4), f;
  if (SIM_IS_DJI) {
    reject(&s, q);
    reject(&s, query(id, 8));
    printf("PASS status query: DJI profile %d rejects DM requests\n", SIM_PROFILE);
    return 0;
  }

  unsigned last_id = SIM_IS_BROADCAST ? 4 : id;
  for (unsigned node = id; node <= last_id; ++node) {
    for (unsigned length = 4; length <= 8; length += 4) {
      q = query(node, length);
      SimDM before = s.dm;
      assert(MotorSim_OnFrame(&s, &q, 0));
      assert(memcmp(&s.dm, &before, sizeof(before)) == 0);
      f = drain(&s, node);
      assert((f.data[0] >> 4) == (SIM_IS_BROADCAST ? 1 : 0));
    }
  }

  for (unsigned length = 0; length <= 9; ++length)
    if (length != 4 && length != 8) reject(&s, query(id, length));
  q = query(id, 4); q.extended = 1; reject(&s, q);
  q = query(id, 4); q.data[2] = 0x33; reject(&s, q);
  q = query(id, 4); q.data[3] = 1; reject(&s, q);
  for (unsigned i = 4; i < 8; ++i) {
    q = query(id, 8); q.data[i] = 1; reject(&s, q);
  }
  reject(&s, query(0, 4));
  reject(&s, query(SIM_IS_BROADCAST ? 5 : id+1, 4));
  reject(&s, query(0x100+id, 4)); /* Full 16-bit ID, no low-byte alias. */
  reject(&s, query(0x123, 4));
  reject(&s, query(0xFFFF, 8));
  assert(s.reply_count == 0);

  q = query(id, 4);
  if (SIM_IS_DM) {
    CAN_Frame enable = {.id=SIM_DM_CONTROL_ID, .length=8,
      .data={255,255,255,255,255,255,255,0xFC}};
    assert(MotorSim_OnFrame(&s, &enable, 0));
    drain(&s, id);
    assert(s.dm.status == 1 && s.dm.commands == 1);
    /* Repeated reads must not act as control commands or keep a drive alive. */
    for (unsigned t = 10; t <= SIM_COMMAND_TIMEOUT_MS+10; t += 10) {
      assert(MotorSim_OnFrame(&s, &q, t));
      f = drain(&s, id);
      assert((f.data[0] >> 4) == (t < SIM_COMMAND_TIMEOUT_MS ? 1 : 13));
      assert(s.dm.last_command == 0 && s.dm.commands == 1);
      assert(s.dm.p_des == 0 && s.dm.v_des == 0 && s.dm.t_ff == 0);
    }
    assert(s.timeouts == 1);
  } else {
    CAN_Frame previous = {0};
    unsigned changed = 0;
    for (unsigned t = 0; t < 1000; t += 10) {
      assert(MotorSim_OnFrame(&s, &q, t));
      f = drain(&s, id);
      if (t && memcmp(f.data, previous.data, 8)) ++changed;
      previous = f;
    }
    assert(changed > 90);
    MotorSim_Update(&s, 2000);
    while (MotorSim_PeekFeedback(&s, &f)) {
      assert(s.selected_kind != 1);
      MotorSim_CommitFeedback(&s);
    }
  }

  MotorSim_Init(&s, 0);
  for (unsigned i = 0; i < SIM_DM_REPLY_QUEUE+2; ++i)
    assert(MotorSim_OnFrame(&s, &q, 0));
  assert(s.reply_count == SIM_DM_REPLY_QUEUE && s.reply_dropped == 2);
  unsigned count = 0;
  while (MotorSim_PeekFeedback(&s, &f)) {
    if (s.selected_kind == 1) ++count;
    MotorSim_CommitFeedback(&s);
  }
  assert(count == SIM_DM_REPLY_QUEUE);
  printf("PASS status query: profile %d, DLC4/8, IDs, read-only, timeout, queue\n", SIM_PROFILE);
}
