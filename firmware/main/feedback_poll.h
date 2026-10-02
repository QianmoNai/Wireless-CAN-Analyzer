#pragma once
#include "ble_bridge.h"
#define POLL_MAX 32
// Wire event 8 byte 3. Arbitration alone is contention, not a bus error.
enum { POLL_ERR_ARB=1, POLL_ERR_ACK=2, POLL_ERR_BIT=4, POLL_ERR_FORM=8,
       POLL_ERR_STUFF=16, POLL_ERR_DRIVER=32, POLL_ERR_BUS_OFF=64, POLL_ERR_UNKNOWN=128 };
#define POLL_ERROR_LIMIT 5
#define POLL_ARBITRATION_LIMIT 32
// A conservative 240 bits/query+reply; reserve at most 60% of the CAN bus.
typedef struct { uint32_t id, hz, sent, actual; uint8_t failures, arbitration, detail; int64_t due, lease; } poll_entry_t;
typedef struct { poll_entry_t entries[POLL_MAX]; } feedback_poll_t;
void poll_clear(feedback_poll_t *p, uint8_t reason);
void poll_bus_off(feedback_poll_t *p);
void poll_command(feedback_poll_t *p, const bridge_packet_t *cmd, uint32_t bitrate, int64_t now);
void poll_command_available(feedback_poll_t *p, const bridge_packet_t *cmd,
                            uint32_t bitrate, int64_t now, bool can_available);
void poll_cancel_command(const bridge_packet_t *cmd);
uint32_t poll_next(feedback_poll_t *p, int64_t now);
void poll_result(feedback_poll_t *p, uint32_t id, bool ok, uint8_t detail, int64_t now);
void poll_report(feedback_poll_t *p, int64_t elapsed);
void poll_expire(feedback_poll_t *p,int64_t now);
bool poll_active(const feedback_poll_t *p);
