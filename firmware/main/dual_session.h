#pragma once
#include "dual_protocol.h"
typedef struct {
  uint8_t role, key[32], local[16], remote[16], traffic_key[32];
  uint32_t hello_tx, hello_rx, tx_sequence, rx_sequence, last_seen, generation;
  bool ready, have_remote, online, traffic_ready;
} dual_session_t;
bool dual_session_start(dual_session_t *s, unsigned role,
                        const uint8_t key[32]);
size_t dual_session_hello(dual_session_t *s, uint8_t out[DUAL_MAX_PACKET]);
bool dual_session_receive_hello(dual_session_t *s, const uint8_t *p, size_t n,
                                uint32_t now);
size_t dual_session_encode(const dual_session_t *s, unsigned type,
                           uint32_t sequence, const uint8_t *data, size_t len,
                           uint8_t out[DUAL_MAX_PACKET]);
bool dual_session_decode(const dual_session_t *s, const uint8_t *p, size_t n,
                         uint32_t *sequence, uint8_t *plain, size_t *len);
