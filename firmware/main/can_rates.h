#pragma once
#include "ble_bridge.h"

typedef struct {
    uint32_t rx, tx, bitrate;
    int64_t started_us;
} can_rates_t;

void can_rates_reset(can_rates_t *s, uint32_t rx, uint32_t tx, uint32_t bitrate, int64_t now);
bool can_rates_snapshot(can_rates_t *s, bridge_packet_t *p, uint32_t rx, uint32_t tx, int64_t now);
