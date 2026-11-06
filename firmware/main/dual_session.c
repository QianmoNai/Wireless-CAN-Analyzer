#include "dual_session.h"
static bool derive_key(const dual_session_t *s, uint8_t key[32]);
bool dual_session_start(dual_session_t *s, unsigned role,
                        const uint8_t key[32]) {
  uint8_t copy[32];
  memcpy(copy, key, 32);
  uint32_t generation = s->generation + 1;
  dual_zero(s, sizeof(*s));
  s->role = role;
  s->generation = generation;
  memcpy(s->key, copy, 32);
  dual_zero(copy, 32);
  s->ready = dual_random(s->local, 16);
  return s->ready;
}
size_t dual_session_hello(dual_session_t *s, uint8_t out[DUAL_MAX_PACKET]) {
  if (!s->ready || s->hello_tx == UINT32_MAX)
    return 0;
  size_t n = dual_header(out, DUAL_HELLO, s->role, 52);
  uint8_t *v = out + DUAL_HEADER, tag[32];
  memcpy(v, s->local, 16);
  memcpy(v + 16, s->remote, 16);
  dual_put32(v + 32, ++s->hello_tx);
  if (!dual_mac(s->key, out, n - 16, tag))
    return 0;
  memcpy(v + 36, tag, 16);
  return n;
}
bool dual_session_receive_hello(dual_session_t *s, const uint8_t *p, size_t n,
                                uint32_t now) {
  if (!s->ready || !dual_packet_valid(p, n, s->role) || p[4] != DUAL_HELLO ||
      n != 60)
    return false;
  uint8_t tag[32];
  const uint8_t *v = p + DUAL_HEADER;
  if (!dual_mac(s->key, p, n - 16, tag) || !dual_equal(tag, v + 36, 16))
    return false;
  uint32_t sequence = dual_u32(v + 32);
  if (!sequence)
    return false;
  if (s->role == DUAL_A) {
    if (!dual_equal(v + 16, s->local, 16))
      return false;
    if (s->have_remote && !dual_equal(v, s->remote, 16)) {
      // A owns the session challenge. A new B must prove possession using
      // a NEW A challenge, so replaying an old B boot cannot replay CAN.
      dual_session_start(s, s->role, s->key);
      return false;
    }
    if (!s->have_remote) {
      memcpy(s->remote, v, 16);
      s->have_remote = true;
      s->hello_rx = 0;
    }
  } else if (!s->have_remote || !dual_equal(v, s->remote, 16)) {
    if (!dual_session_start(s, s->role, s->key))
      return false;
    memcpy(s->remote, v, 16);
    s->have_remote = true;
  }
  if (sequence <= s->hello_rx)
    return false;
  s->hello_rx = sequence;
  if (dual_equal(v + 16, s->local, 16)) {
    if (!s->traffic_ready) {
      if (!derive_key(s, s->traffic_key)) {
        dual_zero(s->traffic_key, sizeof(s->traffic_key));
        return false;
      }
      s->traffic_ready = true;
    }
    s->online = true;
    s->last_seen = now;
  }
  return true;
}
static bool derive_key(const dual_session_t *s, uint8_t key[32]) {
  uint8_t context[48];
  memcpy(context, "CAN-SESSION-v1", 15);
  context[15] = 0;
  memcpy(context + 16, s->role == DUAL_A ? s->local : s->remote, 16);
  memcpy(context + 32, s->role == DUAL_A ? s->remote : s->local, 16);
  return dual_mac(s->key, context, sizeof(context), key);
}
static void nonce(uint8_t out[13], unsigned role, unsigned type, uint32_t seq) {
  memset(out, 0, 13);
  out[0] = role;
  out[1] = type;
  dual_put32(out + 9, seq);
}
size_t dual_session_encode(const dual_session_t *s, unsigned type, uint32_t seq,
                           const uint8_t *data, size_t len,
                           uint8_t out[DUAL_MAX_PACKET]) {
  if (!s->online || !s->traffic_ready || !seq || (type != DUAL_DATA && type != DUAL_ACK && type != DUAL_UART) ||
      len > 190)
    return 0;
  size_t n = dual_header(out, type, s->role, 36 + len + 16);
  uint8_t *v = out + DUAL_HEADER, iv[13];
  memcpy(v, s->local, 16);
  memcpy(v + 16, s->remote, 16);
  dual_put32(v + 32, seq);
  nonce(iv, s->role, type, seq);
  bool ok = dual_seal(s->traffic_key, iv, out, 44, data, len, out + 44);
  return ok ? n : 0;
}
bool dual_session_decode(const dual_session_t *s, const uint8_t *p, size_t n,
                         uint32_t *seq, uint8_t *plain, size_t *len) {
  if (!s->online || !s->traffic_ready || !dual_packet_valid(p, n, s->role) || n < 60 ||
      (p[4] != DUAL_DATA && p[4] != DUAL_ACK && p[4] != DUAL_UART))
    return false;
  const uint8_t *v = p + DUAL_HEADER;
  *seq = dual_u32(v + 32);
  if (!*seq || !dual_equal(v, s->remote, 16) ||
      !dual_equal(v + 16, s->local, 16))
    return false;
  uint8_t iv[13];
  nonce(iv, p[5], p[4], *seq);
  bool ok = dual_open(s->traffic_key, iv, p, 44, p + 44, n - 44, plain);
  if (ok)
    *len = n - 60;
  return ok;
}
