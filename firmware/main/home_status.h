#pragma once
#include "ble_bridge.h"
#define HOME_MOTORS 32
typedef struct { uint32_t id, feedback; uint8_t family; bool seen; int64_t last; } home_motor_t;
typedef struct {
 home_motor_t motors[HOME_MOTORS], staged[HOME_MOTORS];
 uint32_t revision, staged_revision, staged_mask; uint8_t count, mode, target; bool quiet, dirty; bridge_packet_t latest; int64_t next_sample;
 uint64_t imu_due[4];
 uint32_t sample_hz,received,sampled,rate_stamp; bool rate_seen; uint64_t sample_time,sample_due; int64_t stats_start;
} home_status_t;
// Caller serializes access; commands never generate CAN frames.
bool home_command(home_status_t *s,const bridge_packet_t *p,bridge_packet_t *ack);
void home_observe(home_status_t *s,const bridge_packet_t *p,int64_t now_ms);
bool home_detail(home_status_t *s,bridge_packet_t *p,int64_t now_ms);
void home_snapshot(const home_status_t *s,bridge_packet_t *p,int64_t now_ms);

bool home_detail_stats(home_status_t *s,bridge_packet_t *p,int64_t now_ms);
