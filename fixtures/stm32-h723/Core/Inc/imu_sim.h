#ifndef IMU_SIM_H
#define IMU_SIM_H
#include <stdbool.h>
#include "can_frame.h"
#include "motor_sim_config.h"

/* Independent of motor profile. Default: add IMU to the mixed test profile. */
#ifndef SIM_IMU_ENABLED
#define SIM_IMU_ENABLED SIM_IS_BROADCAST
#endif
#ifndef SIM_IMU_CAN_ID
#define SIM_IMU_CAN_ID 0x050U
#endif
#ifndef SIM_IMU_MASTER_ID
#define SIM_IMU_MASTER_ID 0x060U
#endif
#ifndef SIM_IMU_PERIOD_MS
#define SIM_IMU_PERIOD_MS 10U
#endif
#ifndef SIM_IMU_ACTIVE
#define SIM_IMU_ACTIVE 1
#endif
#define SIM_IMU_QUEUE_SIZE 16U

typedef struct {
  uint32_t tick, phase, last_sample, period_ms;
  uint32_t requests, rejected, dropped;
  bool active, prefer_reply;
  CAN_Frame periodic[4], replies[SIM_IMU_QUEUE_SIZE];
  unsigned pending, cursor, head, tail, count, selected, selected_index;
} ImuSim;
void ImuSim_Init(ImuSim *s, uint32_t now);
void ImuSim_Update(ImuSim *s, uint32_t now);
bool ImuSim_OnFrame(ImuSim *s, const CAN_Frame *f, uint32_t now);
bool ImuSim_PeekFeedback(ImuSim *s, CAN_Frame *f);
void ImuSim_CommitFeedback(ImuSim *s);
#endif
