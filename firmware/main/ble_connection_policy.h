#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t generation;
    int64_t due_ms;
    unsigned attempts;
    bool finished;
} bridge_ble_policy_t;
typedef struct { uint16_t min_interval, max_interval, latency, timeout; } bridge_ble_params_t;

// Host event-queue context only. One accepted request, at most two local attempts.
void bridge_ble_policy_start(bridge_ble_policy_t *,uint32_t generation,int64_t now_ms);
bool bridge_ble_policy_next(bridge_ble_policy_t *,uint32_t generation,int64_t now_ms,
                            unsigned profile,bool ready,uint16_t interval,
                            uint16_t latency,uint16_t timeout,bridge_ble_params_t *);
void bridge_ble_policy_result(bridge_ble_policy_t *,int rc);
