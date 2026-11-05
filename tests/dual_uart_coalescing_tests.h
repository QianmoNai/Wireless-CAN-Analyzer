// White-box checks of the actual prepare path, compiled only into host runtime A.
#include <stdio.h>
static void coalesce_queue(size_t size, uint8_t value, uint32_t age, uint32_t epoch) {
  uart_message_t m = {.size = size, .created = now_ms() - age, .generation = epoch};
  memset(m.data, value, sizeof(m.data));
  assert(xQueueSend(uart_out, &m, 0) == pdTRUE);
}
static void coalesce_reset(void) {
  xQueueReset(uart_out);
  pending_size = pending_count = 0;
  session.online = true;
  live_uart = true;
  atomic_store(&requested_uart, true);
  atomic_store(&requested_role, DUAL_A);
  atomic_store(&accepting, true);
  atomic_store(&reset_data, false);
}
static size_t coalesce_plain(uint8_t data[DUAL_UART_CHUNK]) {
  dual_session_t peer = session;
  peer.role = DUAL_B;
  memcpy(peer.local, session.remote, 16);
  memcpy(peer.remote, session.local, 16);
  uint32_t seq = 0;
  size_t size = 0;
  assert(pending_size <= DUAL_MAX_PACKET && pending_size <= 250);
  assert(dual_session_decode(&peer, pending, pending_size, &seq, data, &size));
  assert(size <= 190 && size == pending_count && seq == pending_sequence);
  assert(pending_type == DUAL_UART && attempts == 0 && next_retry == 0);
  return size;
}
static unsigned coalesce_hook_action, coalesce_hook_count;
static void coalesce_hook(QueueHandle_t q) {
  if (q != uart_out) return;
  if (++coalesce_hook_count != 2) return;
  sim_queue_after_receive = NULL;
  switch (coalesce_hook_action) {
  case 0: dual_link_reset_data(); break;
  case 1: dual_link_set_uart(false); break;
  case 2: dual_link_request_mode(0); break;
  case 3: session.online = false; break;
  case 4: atomic_fetch_add(&generation, 1); break;
  case 5: coalesce_queue(10, 9, 0, atomic_load(&generation)); break;
  default: assert(false);
  }
}
void dual_a_test_uart_coalescing(void) {
  assert(!pending_size && !uxQueueMessagesWaiting(uart_out));
  dual_session_t saved_session = session;
  uint32_t saved_generation = atomic_load(&generation);
  uint32_t saved_dropped = atomic_load(&uart_drop_count);
  uint8_t data[DUAL_UART_CHUNK], wire[DUAL_MAX_PACKET];
  uint32_t epoch = saved_generation;
  coalesce_reset();
  // One queued block is prepared immediately; bytes and timestamp stay intact.
  coalesce_queue(37, 0x35, 7, epoch);
  prepare_uart(now_ms());
  assert(coalesce_plain(data) == 37 && pending_at == now_ms() - 7);
  for (unsigned i = 0; i < 37; i++) assert(data[i] == 0x35);
  // An existing pending packet cannot be replaced, extended, or resequenced.
  memcpy(wire, pending, pending_size);
  size_t wire_size = pending_size;
  uint32_t seq = pending_sequence;
  coalesce_queue(10, 8, 0, epoch);
  prepare_uart(now_ms());
  assert(pending_size == wire_size && pending_sequence == seq &&
         !memcmp(pending, wire, wire_size) && uxQueueMessagesWaiting(uart_out) == 1);
  // 64+64+64 cannot become 192: retain the third block at the queue head.
  coalesce_reset();
  for (unsigned i = 1; i <= 3; i++) coalesce_queue(64, i, i, epoch);
  prepare_uart(now_ms());
  assert(coalesce_plain(data) == 128 && pending_at == now_ms() - 2);
  for (unsigned i = 0; i < 128; i++) assert(data[i] == (i < 64 ? 1 : 2));
  assert(uxQueueMessagesWaiting(uart_out) == 1);
  pending_size = pending_count = 0;
  prepare_uart(now_ms());
  assert(coalesce_plain(data) == 64 && pending_sequence == seq + 2);
  for (unsigned i = 0; i < 64; i++) assert(data[i] == 3);
  // Exact 190-byte limit is a 250-byte wire packet, with FIFO byte order.
  coalesce_reset();
  coalesce_queue(64, 1, 0, epoch);coalesce_queue(64, 2, 0, epoch);
  coalesce_queue(62, 3, 0, epoch);coalesce_queue(1, 4, 0, epoch);
  prepare_uart(now_ms());
  assert(coalesce_plain(data) == 190 && pending_size == 250);
  for (unsigned i = 0; i < 190; i++) assert(data[i] == (i < 64 ? 1 : i < 128 ? 2 : 3));
  assert(uxQueueMessagesWaiting(uart_out) == 1);
  // Do not split or skip a long-but-valid block to fit later small blocks.
  coalesce_reset();
  coalesce_queue(30, 1, 0, epoch);coalesce_queue(180, 2, 0, epoch);
  coalesce_queue(20, 3, 0, epoch);
  prepare_uart(now_ms());assert(coalesce_plain(data) == 30);
  pending_size = pending_count = 0;
  prepare_uart(now_ms());assert(coalesce_plain(data) == 180);
  assert(uxQueueMessagesWaiting(uart_out) == 1);
  // API rejects >190 B; malformed oversized queued blocks never reach encode/copy.
  assert(!dual_link_uart_rx(data, 191));
  coalesce_reset();
  uint32_t dropped = atomic_load(&uart_drop_count);
  coalesce_queue(20, 1, 0, epoch);coalesce_queue(191, 2, 0, epoch);
  coalesce_queue(10, 3, 0, epoch);
  prepare_uart(now_ms());assert(coalesce_plain(data) == 20);
  pending_size = pending_count = 0;
  prepare_uart(now_ms());assert(coalesce_plain(data) == 10 && data[0] == 3);
  assert(atomic_load(&uart_drop_count) == dropped + 191);
  // Generation boundary ends the batch, even when a following item matches.
  coalesce_reset();dropped = atomic_load(&uart_drop_count);
  coalesce_queue(20, 1, 0, epoch);coalesce_queue(10, 2, 0, epoch - 1);
  coalesce_queue(30, 3, 0, epoch);
  prepare_uart(now_ms());assert(coalesce_plain(data) == 20);
  assert(uxQueueMessagesWaiting(uart_out) == 2);
  pending_size = pending_count = 0;
  prepare_uart(now_ms());assert(coalesce_plain(data) == 30 && data[0] == 3);
  assert(atomic_load(&uart_drop_count) == dropped + 10);
  // Per-item expiry and accurate byte drops; do not merge across an expired gap.
  coalesce_reset();dropped = atomic_load(&uart_drop_count);
  coalesce_queue(5, 0, CAN_DUAL_UART_TTL_MS, epoch);
  coalesce_queue(20, 1, CAN_DUAL_UART_TTL_MS - 1, epoch);
  coalesce_queue(10, 2, CAN_DUAL_UART_TTL_MS, epoch);
  coalesce_queue(30, 3, 0, epoch);
  prepare_uart(now_ms());assert(coalesce_plain(data) == 20);
  assert(pending_at == now_ms() - (CAN_DUAL_UART_TTL_MS - 1));
  assert(atomic_load(&uart_drop_count) == dropped + 15);
  assert(uxQueueMessagesWaiting(uart_out) == 1);
  // Oldest creation time wins across uint32_t wrap, never a fresh prepare TTL.
  coalesce_reset();
  coalesce_queue(20, 1, 8, epoch);coalesce_queue(20, 2, 15, epoch);
  uint32_t wrap_now = 5;
  uart_message_t m;
  assert(xQueueReceive(uart_out, &m, 0));m.created = wrap_now - 8;
  xQueueReset(uart_out);assert(xQueueSend(uart_out, &m, 0));
  m.created = wrap_now - 15;assert(xQueueSend(uart_out, &m, 0));
  uint32_t saved_now=now_ms();sim_clock_set(wrap_now);
  prepare_uart(now_ms());assert(coalesce_plain(data) == 40 && pending_at == wrap_now - 15);
  sim_clock_set(saved_now);
  // Reset, UART cancellation, role exit, offline, or epoch change during collection.
  for (unsigned action = 0; action < 5; action++) {
    coalesce_reset();epoch = atomic_load(&generation);
    dropped = atomic_load(&uart_drop_count);
    coalesce_queue(20, 1, 0, epoch);coalesce_queue(30, 2, 0, epoch);
    coalesce_queue(40, 3, 0, epoch);
    coalesce_hook_action = action;coalesce_hook_count = 0;
    sim_queue_after_receive = coalesce_hook;
    prepare_uart(now_ms());
    assert(!sim_queue_after_receive && !pending_size && !pending_count);
    assert(atomic_load(&uart_drop_count) == dropped + 50);
    purge();
    assert(!uxQueueMessagesWaiting(uart_out) &&
           atomic_load(&uart_drop_count) == dropped + 90);
  }
  // Already prepared merged bytes are counted once on disconnect/purge.
  coalesce_reset();epoch = atomic_load(&generation);
  dropped = atomic_load(&uart_drop_count);
  coalesce_queue(64, 1, 0, epoch);coalesce_queue(64, 2, 0, epoch);coalesce_queue(64, 3, 0, epoch);
  prepare_uart(now_ms());assert(coalesce_plain(data) == 128);
  purge();assert(!pending_size && atomic_load(&uart_drop_count) == dropped + 192);
  // A producer adding a block during prepare cannot extend the snapshot budget.
  coalesce_reset();epoch = atomic_load(&generation);
  coalesce_queue(20, 1, 0, epoch);coalesce_queue(30, 2, 0, epoch);
  coalesce_hook_action = 5;coalesce_hook_count = 0;sim_queue_after_receive = coalesce_hook;
  prepare_uart(now_ms());assert(coalesce_plain(data) == 50);
  assert(uxQueueMessagesWaiting(uart_out) == 1);
  // Leave the paired two-device integration test exactly as it was.
  coalesce_reset();session = saved_session;
  atomic_store(&generation, saved_generation);
  atomic_store(&uart_drop_count, saved_dropped);
  puts("UART prepare: bounds/order/oldest TTL/generation/cancel/purge/snapshot/single pending PASS");
}
