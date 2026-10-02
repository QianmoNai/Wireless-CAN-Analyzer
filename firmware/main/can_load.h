#pragma once
#include <stdatomic.h>
#include "ble_bridge.h"

// Frame-length estimate includes intermission, excludes stuffing/error/retries.
// ISR writers share only lock-free 32-bit counters. Window state belongs to the
// CAN owner task; it never shares its 64-bit clock with the ISR.
typedef struct {
    _Atomic uint32_t rx_bits, tx_bits;
    uint32_t previous_rx, previous_tx, bitrate;
    int64_t started_us;
} can_load_t;

uint32_t can_load_frame_bits(bool extended, bool remote, unsigned dlc);
void can_load_rx(can_load_t *load, bool extended, bool remote, unsigned dlc);
void can_load_tx_result(can_load_t *load, uint32_t frame_bits, bool successful);
void can_load_reset(can_load_t *load, uint32_t bitrate, int64_t now_us);
// Produces event 13 only after a full second, using the actual elapsed window.
bool can_load_snapshot(can_load_t *load, bridge_packet_t *packet, int64_t now_us);
