#include "dual_protocol.h"
size_t dual_header(uint8_t *p, unsigned type, unsigned role, size_t size) {
  memcpy(p, "CDL1", 4);
  p[4] = type;
  p[5] = role;
  p[6] = size;
  p[7] = size >> 8;
  return DUAL_HEADER + size;
}
bool dual_packet_valid(const uint8_t *p, size_t n, unsigned role) {
  return n >= DUAL_HEADER && n <= DUAL_MAX_PACKET && !memcmp(p, "CDL1", 4) &&
         p[5] == (role == DUAL_A ? DUAL_B : DUAL_A) &&
         (size_t)(p[6] | p[7] << 8) == n - DUAL_HEADER && p[4] >= DUAL_BEACON &&
         p[4] <= DUAL_ACK;
}
bool dual_can_valid(const uint8_t p[DUAL_CAN_SIZE]) {
  return !(p[0] & ~3) && p[1] <= 8 &&
         dual_u32(p + 2) <= ((p[0] & 1) ? 0x1fffffffU : 0x7ffU);
}
static bool commitment(unsigned role, const uint8_t mac[6],
                       const uint8_t pub[65], const uint8_t salt[32],
                       uint8_t out[32]) {
  uint8_t b[112];
  memcpy(b, "PAIR-C1", 8);
  b[8] = role;
  memcpy(b + 9, mac, 6);
  memcpy(b + 15, pub, 65);
  memcpy(b + 80, salt, 32);
  return dual_hash(b, sizeof(b), out);
}
void dual_pair_clear(dual_pair_t *p) {
  dual_key_free(&p->private_key);
  dual_zero(p, sizeof(*p));
}
bool dual_pair_begin(dual_pair_t *p, unsigned role, const uint8_t mac[6],
                     uint32_t now) {
  dual_pair_clear(p);
  if (role != DUAL_A && role != DUAL_B)
    return false;
  p->role = role;
  p->started = now;
  memcpy(p->mac, mac, 6);
  if (!dual_random(p->salt, 32) || !dual_random(p->id, 16) ||
      !dual_key_create(&p->private_key, p->pub) ||
      !commitment(role, mac, p->pub, p->salt, p->commit)) {
    dual_pair_clear(p);
    return false;
  }
  p->active = true;
  return true;
}
bool dual_pair_select(dual_pair_t *p, const uint8_t peer[6],
                      const uint8_t commit[32]) {
  if (!p->active || p->role != DUAL_A || p->selected || (peer[0] & 1) ||
      !memcmp(peer, p->mac, 6))
    return false;
  memcpy(p->peer, peer, 6);
  memcpy(p->peer_commit, commit, 32);
  p->selected = true;
  return true;
}
static bool derive(dual_pair_t *p, const uint8_t pub[65],
                   const uint8_t salt[32]) {
  uint8_t secret[32], transcript[238], digest[32], prk[32], code[32];
  bool a = p->role == DUAL_A;
  memcpy(transcript, "CAN-DUAL-PAIR-v1", 16);
  memcpy(transcript + 16, p->id, 16);
  memcpy(transcript + 32, a ? p->mac : p->peer, 6);
  memcpy(transcript + 38, a ? p->peer : p->mac, 6);
  memcpy(transcript + 44, a ? p->pub : pub, 65);
  memcpy(transcript + 109, a ? pub : p->pub, 65);
  memcpy(transcript + 174, a ? p->salt : salt, 32);
  memcpy(transcript + 206, a ? salt : p->salt, 32);
  bool ok = dual_agree(p->private_key, pub, secret) &&
            dual_hash(transcript, sizeof(transcript), digest) &&
            dual_mac(digest, secret, 32, prk) &&
            dual_mac(prk, "CAN-DUAL-KEY-v1\x01", 16, p->key) &&
            dual_mac(p->key, "CAN-DUAL-SAS-v1", 15, code);
  if (ok) {
    p->code = dual_u32(code) % 1000000;
    p->derived = true;
  }
  dual_zero(secret, sizeof(secret));
  dual_zero(prk, sizeof(prk));
  dual_zero(code, sizeof(code));
  return ok;
}
bool dual_pair_receive(dual_pair_t *p, const uint8_t from[6],
                       const uint8_t *data, size_t n) {
  if (!p->active || !dual_packet_valid(data, n, p->role) || (from[0] & 1) ||
      !memcmp(from, p->mac, 6))
    return false;
  const uint8_t *v = data + DUAL_HEADER;
  size_t len = n - DUAL_HEADER;
  unsigned type = data[4];
  if (type == DUAL_REQUEST && len == 80 && p->role == DUAL_B && !p->selected) {
    if (!dual_equal(v + 48, p->commit, 32))
      return false;
    memcpy(p->id, v, 16);
    memcpy(p->peer, from, 6);
    memcpy(p->peer_commit, v + 16, 32);
    p->selected = p->committed = true;
    return true;
  }
  if (!p->selected || memcmp(from, p->peer, 6) || len < 16 ||
      !dual_equal(v, p->id, 16))
    return false;
  if (type == DUAL_COMMIT && len == 80 && p->role == DUAL_A) {
    if (!dual_equal(v + 16, p->peer_commit, 32) ||
        !dual_equal(v + 48, p->commit, 32))
      return false;
    p->committed = true;
    return true;
  }
  if (type == DUAL_REVEAL && len == 113 && p->committed) {
    uint8_t hash[32];
    if (!commitment(data[5], from, v + 16, v + 81, hash) ||
        !dual_equal(hash, p->peer_commit, 32))
      return false;
    if (p->derived)
      return true;
    return derive(p, v + 16, v + 81);
  }
  if (type == DUAL_CONFIRM && len == 32 && p->derived) {
    uint8_t tag[32];
    if (!dual_mac(p->key, data, n - 16, tag) || !dual_equal(tag, v + 16, 16))
      return false;
    p->remote_confirmed = true;
    return true;
  }
  return false;
}
size_t dual_pair_message(dual_pair_t *p, uint8_t out[DUAL_MAX_PACKET]) {
  if (!p->active)
    return 0;
  uint8_t *v = out + DUAL_HEADER;
  unsigned round = p->round++;
  if (!p->selected) {
    if (p->role != DUAL_B)
      return 0;
    memcpy(v, p->commit, 32);
    return dual_header(out, DUAL_BEACON, p->role, 32);
  }
  memcpy(v, p->id, 16);
  if (p->local_confirmed && p->derived && (round % 3 == 2)) {
    size_t n = dual_header(out, DUAL_CONFIRM, p->role, 32);
    uint8_t tag[32];
    if (!dual_mac(p->key, out, n - 16, tag))
      return 0;
    memcpy(v + 16, tag, 16);
    return n;
  }
  if (!p->committed ||
      (p->role == DUAL_B && !p->remote_confirmed && (round % 3 == 0))) {
    memcpy(v + 16, p->commit, 32);
    memcpy(v + 48, p->peer_commit, 32);
    return dual_header(out, p->role == DUAL_A ? DUAL_REQUEST : DUAL_COMMIT,
                       p->role, 80);
  }
  memcpy(v + 16, p->pub, 65);
  memcpy(v + 81, p->salt, 32);
  return dual_header(out, DUAL_REVEAL, p->role, 113);
}
