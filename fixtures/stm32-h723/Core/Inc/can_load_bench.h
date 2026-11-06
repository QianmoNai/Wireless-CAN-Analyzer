#ifndef CAN_LOAD_BENCH_H
#define CAN_LOAD_BENCH_H
#include <stdbool.h>
#include "can_frame.h"

/* Opt-in dual-bus fixture only. These IDs are test data, never motor commands. */
#define CAN_LOAD_MAX_FRAMES 60000U
#define CAN_LOAD_ID_A 0x6A0U
#define CAN_LOAD_ID_B 0x6B0U
typedef struct {
  uint32_t tx_queued, tx_busy, tx_errors, skipped_slots, tx_done_ms;
  uint32_t rx_unique, rx_duplicates, rx_out_of_order, rx_gap_events;
  uint32_t rx_bad, rx_stale, rx_last_sequence, rx_first_ms, rx_last_ms;
} CAN_LoadStats;
typedef struct {
  /* SWD writes config first, request last. STOP=0, START=1. */
  uint32_t command, rate_hz, frames, nonce, request, done, result, active;
  uint32_t started_ms, finished_ms;
  CAN_LoadStats bus[2];
} CAN_LoadControl;
typedef struct {
  uint32_t rate_hz, frames, nonce, started_ms, slot[2], next_due[2], next_rx[2];
  bool receiving;
  uint8_t seen[2][(CAN_LOAD_MAX_FRAMES+7U)/8U];
} CAN_LoadState;
/* Callback result: 0=queued, 1=busy/no enqueue, 2=error/no enqueue. */
typedef unsigned (*CAN_LoadSend)(unsigned bus, const CAN_Frame *frame, void *context);
void CAN_LoadPoll(volatile CAN_LoadControl *, CAN_LoadState *, uint32_t now,
                  bool allowed, CAN_LoadSend send, void *context);
bool CAN_LoadReceive(volatile CAN_LoadControl *, CAN_LoadState *, unsigned bus,
                     const CAN_Frame *, uint32_t now);
#endif
