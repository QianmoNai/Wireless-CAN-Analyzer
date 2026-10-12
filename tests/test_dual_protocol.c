#include "dual_session.h"
#include <assert.h>
#include <stdio.h>
static const uint8_t a_mac[6] = {2, 1, 2, 3, 4, 5},
                     b_mac[6] = {2, 1, 2, 3, 4, 6},
                     other[6] = {2, 1, 2, 3, 4, 7};
static void exchange(dual_pair_t *a, dual_pair_t *b, unsigned rounds,
                     bool loss) {
  uint8_t packet[250];
  for (unsigned i = 0; i < rounds; i++) {
    size_t n = dual_pair_message(a, packet);
    if (n && (!loss || i % 7))
      dual_pair_receive(b, a_mac, packet, n);
    n = dual_pair_message(b, packet);
    if (n && (!loss || i % 5))
      dual_pair_receive(a, b_mac, packet, n);
  }
}
static void connect_sessions(dual_session_t *a, dual_session_t *b,
                             uint32_t now) {
  uint8_t p[250];
  for (unsigned i = 0; i < 8; i++) {
    size_t n = dual_session_hello(a, p);
    assert(n == 60);
    dual_session_receive_hello(b, p, n, now + i);
    n = dual_session_hello(b, p);
    assert(n == 60);
    dual_session_receive_hello(a, p, n, now + i);
  }
  assert(a->online && b->online);
}
int main(void) {
  dual_pair_t a = {0}, b = {0};
  assert(dual_pair_begin(&a, DUAL_A, a_mac, 1));
  assert(dual_pair_begin(&b, DUAL_B, b_mac, 1));
  assert(dual_pair_select(&a, b_mac, b.commit));
  uint8_t p[250], copy[250];
  size_t n = dual_pair_message(&a, p);
  assert(n == 88);
  memcpy(copy, p, n);
  copy[70] ^= 1;
  assert(!dual_pair_receive(&b, a_mac, copy, n));
  assert(!b.selected);
  assert(!dual_pair_receive(&b, b_mac, p, n));
  assert(dual_pair_receive(&b, a_mac, p, n));
  assert(!dual_pair_receive(&b, other, p, n));
  exchange(&a, &b, 30, true);
  assert(a.derived && b.derived && a.code == b.code && a.code < 1000000 &&
         dual_equal(a.key, b.key, 32));
  assert(!dual_pair_complete(&a) && !dual_pair_complete(&b));
  a.local_confirmed = true;
  exchange(&a, &b, 15, true);
  assert(b.remote_confirmed && !dual_pair_complete(&a) &&
         !dual_pair_complete(&b));
  b.local_confirmed = true;
  exchange(&a, &b, 30, true);
  assert(dual_pair_complete(&a) && dual_pair_complete(&b));
  // Tamper with authenticated confirmation; only a genuine confirmation counts.
  b.remote_confirmed = false;
  do {
    n = dual_pair_message(&a, p);
  } while (p[4] != DUAL_CONFIRM);
  memcpy(copy, p, n);
  copy[n - 1] ^= 1;
  assert(!dual_pair_receive(&b, a_mac, copy, n) && !b.remote_confirmed);
  assert(dual_pair_receive(&b, a_mac, p, n));
  dual_session_t x = {0}, y = {0};
  assert(dual_session_start(&x, DUAL_A, a.key));
  assert(dual_session_start(&y, DUAL_B, b.key));
  connect_sessions(&x, &y, 100);
  uint8_t frame[15] = {1, 0, 8};
  dual_put32(frame + 3, 0x123);
  for (unsigned i = 0; i < 8; i++)
    frame[7 + i] = i;
  n = dual_session_encode(&x, DUAL_DATA, 1, frame, sizeof(frame), p);
  assert(n == 75);
  uint32_t seq;
  uint8_t decoded[190];
  size_t len;
  assert(dual_session_decode(&y, p, n, &seq, decoded, &len) && seq == 1 &&
         len == sizeof(frame) && !memcmp(decoded, frame, len));
  memcpy(copy, p, n);
  copy[50] ^= 1;
  assert(!dual_session_decode(&y, copy, n, &seq, decoded, &len));
  assert(!dual_session_decode(&x, p, n, &seq, decoded, &len));
  uint8_t ack[250];
  size_t ack_n = dual_session_encode(&y, DUAL_ACK, 1, NULL, 0, ack);
  assert(ack_n == 60);
  assert(dual_session_decode(&x, ack, ack_n, &seq, decoded, &len) && seq == 1 &&
         len == 0);
  // Reboot B: both ends negotiate a fresh session, rejecting captured CAN.
  uint8_t old_hello[250];
  size_t old_n = dual_session_hello(&y, old_hello);
  assert(dual_session_start(&y, DUAL_B, b.key));
  connect_sessions(&x, &y, 1000);
  assert(!dual_session_decode(&y, p, n, &seq, decoded, &len));
  assert(!dual_session_receive_hello(&x, old_hello, old_n, 2000));
  // A valid heartbeat is accepted once, replay cannot extend online lifetime.
  n = dual_session_hello(&y, p);
  assert(dual_session_receive_hello(&x, p, n, 3000));
  assert(!dual_session_receive_hello(&x, p, n, 4000) && x.last_seen == 3000);
  assert(!dual_can_valid((uint8_t[14]){4, 8}));
  assert(!dual_can_valid((uint8_t[14]){0, 9}));
  uint8_t can[14] = {0, 8};
  dual_put32(can + 2, 0x800);
  assert(!dual_can_valid(can));
  can[0] = 1;
  assert(dual_can_valid(can));
  can[0] = 2;
  dual_put32(can + 2, 0x7ff);
  assert(dual_can_valid(can));
  dual_pair_clear(&a);
  dual_pair_clear(&b);
  assert(!a.private_key && !b.private_key);
  // Early B confirmation must still transmit its public key (no deadlock).
  assert(dual_pair_begin(&a, DUAL_A, a_mac, 1));
  assert(dual_pair_begin(&b, DUAL_B, b_mac, 1));
  assert(dual_pair_select(&a, b_mac, b.commit));
  n = dual_pair_message(&a, p);
  assert(dual_pair_receive(&b, a_mac, p, n));
  n = dual_pair_message(&b, p);
  assert(dual_pair_receive(&a, b_mac, p, n));
  n = dual_pair_message(&a, p);
  assert(dual_pair_receive(&b, a_mac, p, n) && b.derived);
  b.local_confirmed = true;
  exchange(&a, &b, 30, true);
  assert(a.derived && a.code == b.code);
  a.local_confirmed = true;
  exchange(&a, &b, 30, true);
  assert(dual_pair_complete(&a) && dual_pair_complete(&b));
  dual_pair_clear(&a);
  dual_pair_clear(&b);
  puts("dual protocol: committed key exchange, SAS, two confirmations, "
       "loss/reorder, real AES-CCM, tamper rejection, fresh reboot sessions "
       "and heartbeat replay rejection PASS");
}
