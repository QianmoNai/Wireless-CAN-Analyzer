#ifndef CAN_BUS_H
#define CAN_BUS_H
#include "stm32h7xx_hal.h"

#include "can_frame.h"

#ifndef CAN_DUAL_BENCH
#define CAN_DUAL_BENCH 0
#endif
extern FDCAN_HandleTypeDef hfdcan1;
#if CAN_DUAL_BENCH
extern FDCAN_HandleTypeDef hfdcan2;
HAL_StatusTypeDef CAN_Bus2_Send(const CAN_Frame *frame);
HAL_StatusTypeDef CAN_Bus2_Receive(CAN_Frame *frame);
/* Bench only: rate=0 stops; otherwise 125/250/500/1000 kbit/s. No pending TX. */
HAL_StatusTypeDef CAN_BusBench_SetRate(unsigned bus, uint32_t rate);
#endif
/* Call after HAL_Init and HSE clock setup. Single foreground caller only. */
HAL_StatusTypeDef CAN_Bus_Init(void);
/* Nonblocking: HAL_OK means queued, HAL_BUSY means FIFO full (no enqueue). */
HAL_StatusTypeDef CAN_Bus_Send(const CAN_Frame *frame);
/* Nonblocking: HAL_BUSY means no frame available. Remote frames are rejected. */
HAL_StatusTypeDef CAN_Bus_Receive(CAN_Frame *frame);
#endif
