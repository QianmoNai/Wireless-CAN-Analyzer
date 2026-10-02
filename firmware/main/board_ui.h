#pragma once
#include "ble_bridge.h"

// Task context only. Bounded snapshot copies; never waits for LCD or flash.
void board_ui_start(void);
void board_ui_observe(const bridge_packet_t *packet);
