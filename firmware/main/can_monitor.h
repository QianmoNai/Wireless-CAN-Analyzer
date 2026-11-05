#pragma once
#include <stdint.h>

enum { CAN_MONITOR_USB_QUERY = 0x1c, CAN_MONITOR_USB_EVENT = 27 };

/* Components of the existing type-4 lifetime drop counter. Read-only; the
 * bridge component includes control/UART delivery, not only raw CAN frames. */
void can_monitor_drop_counters(uint32_t counters[3]);
