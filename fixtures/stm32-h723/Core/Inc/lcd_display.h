#ifndef LCD_DISPLAY_H
#define LCD_DISPLAY_H
#include "can_bus.h"
typedef struct {
  uint32_t tx, busy, rx, errors, lost, bus_off, tx_full;
  uint32_t imu_tx, imu_active, imu_period, imu_dropped;
  CAN_Frame last_rx;
  int32_t dji_rpm, dji_current, dm_position_mrad, dm_velocity_mrad, dm_torque_mnm;
  uint32_t dji_commands, dm_commands, dm_status, reply_dropped, timeouts;
} LCD_Status;
extern volatile uint32_t lcd_ready, lcd_errors;
/* Same 240x240 panel/pins/init sequence as sibling test_h7. */
HAL_StatusTypeDef LCD_Init(void);
/* Single foreground caller; transmits at most one 240-pixel scanline per call. */
void LCD_Process(const LCD_Status *status);
#endif
