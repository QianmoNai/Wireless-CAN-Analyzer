#pragma once
#include <stdint.h>
#include "ble_bridge.h"

enum { BRIDGE_NOTIFY_RAW, BRIDGE_NOTIFY_CONTROL, BRIDGE_NOTIFY_ACK };
typedef struct { bridge_packet_t packet; uint32_t generation; } bridge_notify_work_t;
typedef struct {
    int64_t (*now_us)(void *);
    bool (*pending_raw)(void *);
    bool (*raw_ready)(void *);
    bool (*take)(void *,unsigned,bridge_notify_work_t *);
    void (*deliver)(void *,const bridge_notify_work_t *,unsigned);
    void (*wait_event)(void *,unsigned);
    void (*rest)(void *);
} bridge_notify_schedule_ops_t;
typedef struct { int64_t busy_us; } bridge_notify_schedule_t;

// Each delivery may contain a separately bounded ACK/control retry budget.
// Recheck priority after every packet; raw stays queued while capacity is low.
void bridge_notify_schedule_step(bridge_notify_schedule_t *,
                                const bridge_notify_schedule_ops_t *,void *);
