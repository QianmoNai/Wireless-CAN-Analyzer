#pragma once
#include "dual_crypto.h"
#include <string.h>
enum {
  DUAL_A = 2,
  DUAL_B = 3,
  DUAL_CHANNEL = 6,
  DUAL_MAX_PACKET = 250,
  DUAL_HEADER = 8,
  DUAL_BATCH = 8,
  DUAL_CAN_SIZE = 14,
  DUAL_FRAME_TTL_MS = 100,
  DUAL_PAIR_MS = 60000
};
enum {
  DUAL_BEACON = 1,
  DUAL_REQUEST,
  DUAL_COMMIT,
  DUAL_REVEAL,
  DUAL_CONFIRM,
  DUAL_HELLO,
  DUAL_DATA,
  DUAL_ACK
};
static inline uint32_t dual_u32(const uint8_t *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
         (uint32_t)p[3] << 24;
}
static inline void dual_put32(uint8_t *p, uint32_t n) {
  for (unsigned i = 0; i < 4; i++)
    p[i] = n >> (i * 8);
}
size_t dual_header(uint8_t *p, unsigned type, unsigned role, size_t payload);
bool dual_packet_valid(const uint8_t *p, size_t n, unsigned role);
bool dual_can_valid(const uint8_t p[DUAL_CAN_SIZE]);
// Pairing uses committed ephemeral P-256 keys + random nonces. Both commitments
// are fixed before either public key is revealed. The six-digit SAS
// authenticates the complete transcript only after the human compares and
// confirms BOTH ends.
typedef struct {
  uint8_t role, mac[6], peer[6], id[16], pub[65], salt[32], commit[32],
      peer_commit[32];
  uint8_t key[32];
  dual_private_key_t private_key;
  uint32_t started, code;
  bool active, selected, committed, derived, local_confirmed, remote_confirmed;
  unsigned round;
} dual_pair_t;
bool dual_pair_begin(dual_pair_t *p, unsigned role, const uint8_t mac[6],
                     uint32_t now);
void dual_pair_clear(dual_pair_t *p);
bool dual_pair_select(dual_pair_t *p, const uint8_t peer[6],
                      const uint8_t commit[32]);
bool dual_pair_receive(dual_pair_t *p, const uint8_t from[6],
                       const uint8_t *data, size_t size);
size_t dual_pair_message(dual_pair_t *p, uint8_t out[DUAL_MAX_PACKET]);
// Runtime gates persistence/forwarding on both independent confirmations.
static inline bool dual_pair_complete(const dual_pair_t *p) {
  return p->derived && p->local_confirmed && p->remote_confirmed;
}
