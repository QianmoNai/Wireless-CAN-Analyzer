#ifndef CAN_FRAME_H
#define CAN_FRAME_H
#include <stdint.h>
typedef struct {
  uint32_t id;
  uint8_t extended;
  uint8_t length;
  uint8_t data[8];
  uint8_t remote;
} CAN_Frame;
#endif
