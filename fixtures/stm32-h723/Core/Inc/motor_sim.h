#ifndef MOTOR_SIM_H
#define MOTOR_SIM_H
#include <stdbool.h>
#include "can_frame.h"
#include "motor_sim_config.h"
typedef struct {
  float angle, rpm;
  int16_t current, command_raw;
  uint32_t last_command, commands;
  bool active;
} SimDJI;
typedef struct {
  float position, velocity, torque;
  float p_des, v_des, kp, kd, t_ff;
  float speed_limit, torque_ratio;
  uint32_t last_command, commands;
  uint8_t status; /* 0 disabled, 1 enabled, 13 communication timeout. */
} SimDM;
typedef struct {
  SimDJI dji[SIM_DJI_COUNT];
  SimDM dm;
  uint32_t rejected, reply_dropped, timeouts, skipped_ms;
  uint32_t tick, last_feedback;
  CAN_Frame replies[SIM_DM_REPLY_QUEUE];
  unsigned reply_head, reply_tail, reply_count;
  uint32_t broadcast_phase, broadcast_pending;
  unsigned broadcast_cursor;
  uint32_t dji_pending;
  unsigned dji_cursor, selected_index;
  uint8_t selected_kind, prefer_dm;
} MotorSim;
void MotorSim_Init(MotorSim *sim, uint32_t now);
void MotorSim_Update(MotorSim *sim, uint32_t now);
bool MotorSim_OnFrame(MotorSim *sim, const CAN_Frame *frame, uint32_t now);
/* Peek then commit only after successful hardware enqueue. Single caller. */
bool MotorSim_PeekFeedback(MotorSim *sim, CAN_Frame *frame);
void MotorSim_CommitFeedback(MotorSim *sim);
#endif
