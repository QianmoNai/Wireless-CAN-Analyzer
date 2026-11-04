#include "dual_link.h"
#include "dual_session.h"
#include "esp_mac.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include <stdatomic.h>

#ifndef CAN_DUAL_UART_LOOPBACK_TEST
#define CAN_DUAL_UART_LOOPBACK_TEST 0
#endif
#ifndef CAN_DUAL_UART_RETRY_MS
#define CAN_DUAL_UART_RETRY_MS 25
#endif
#ifndef CAN_DUAL_UART_TTL_MS
#define CAN_DUAL_UART_TTL_MS 1000
#endif

#define RADIO_QUEUE 24
#define CAN_QUEUE 128
#define UART_QUEUE 32
#define CANDIDATES 4
#define RETRY_MS 25
#define RETRIES 3
static const uint8_t broadcast[6] = {255, 255, 255, 255, 255, 255};
typedef struct {
  uint8_t mac[6], data[DUAL_MAX_PACKET];
  uint16_t size;
} radio_message_t;
typedef struct {
  uint8_t data[DUAL_CAN_SIZE];
  uint32_t created, generation;
} can_message_t;
typedef struct {
  uint8_t data[DUAL_UART_CHUNK];
  uint16_t size;
  uint32_t created, generation;
} uart_message_t;
typedef struct {
  uint8_t mac[6], commit[32];
  uint32_t seen;
} candidate_t;
static SemaphoreHandle_t lock;
static QueueHandle_t radio_in, can_out, can_in, uart_out, uart_in, buttons;
static TaskHandle_t worker, can_owner, uart_owner;
static atomic_bool accepting, radio_busy, requested_uart, reset_data;
static bool live_uart;
static atomic_uint uart_drop_count;
static atomic_uint requested_role, live_role;
static atomic_uint generation, drop_count, sent_count, received_count;
static uint8_t mac[6], role, record[48];
static bool running, bound, pair_saved, clear_selected, show_unbind, show_error;
static candidate_t candidates[CANDIDATES];
static unsigned candidate_count, candidate_index;
static dual_pair_t pair;
static dual_session_t session;
static uint32_t next_pair, next_hello, pair_saved_at, ack_sequence;
static uint8_t pending[DUAL_MAX_PACKET];
static size_t pending_size;
static unsigned pending_count, attempts, pending_type;
static uint32_t pending_sequence, pending_at, next_retry;
static uint32_t now_ms(void) { return esp_timer_get_time() / 1000; }
static void wake_worker(void) {
  if (worker)
    xTaskNotifyGive(worker);
}
static void purge(void) {
  atomic_store(&accepting, false);
  atomic_fetch_add(&generation, 1);
  if (uart_owner) xTaskNotifyGive(uart_owner);
  atomic_fetch_add(&drop_count, uxQueueMessagesWaiting(can_out) +
                                    uxQueueMessagesWaiting(can_in) +
                                    (pending_type==DUAL_DATA?pending_count:0));
  uart_message_t discarded;
  while (xQueueReceive(uart_out,&discarded,0)==pdTRUE) atomic_fetch_add(&uart_drop_count,discarded.size);
  while (xQueueReceive(uart_in,&discarded,0)==pdTRUE) atomic_fetch_add(&uart_drop_count,discarded.size);
  if (pending_type==DUAL_UART) atomic_fetch_add(&uart_drop_count,pending_count);
  xQueueReset(can_out);
  xQueueReset(can_in);
  pending_size = 0;
  pending_count = 0;
  ack_sequence = 0;
}
static bool add_peer(const uint8_t address[6]) {
  if (esp_now_is_peer_exist(address))
    return true;
  // Application AES-CCM protects CAN packets. Discovery/key agreement use
  // unencrypted ESP-NOW peers; no shared secret is sent over the air.
  esp_now_peer_info_t peer = {
      .channel = DUAL_CHANNEL, .ifidx = WIFI_IF_STA, .encrypt = false};
  memcpy(peer.peer_addr, address, 6);
  return esp_now_add_peer(&peer) == ESP_OK;
}
static bool send_packet(const uint8_t address[6], const uint8_t *data,
                        size_t n) {
  if (!n || atomic_load(&radio_busy) || !add_peer(address))
    return false;
  atomic_store(&radio_busy, true);
  if (esp_now_send(address, data, n) != ESP_OK) {
    atomic_store(&radio_busy, false);
    return false;
  }
  return true;
}
static void received(const esp_now_recv_info_t *info, const uint8_t *data,
                     int len) {
  if (!info || len < DUAL_HEADER || len > DUAL_MAX_PACKET)
    return;
  radio_message_t m = {.size = len};
  memcpy(m.mac, info->src_addr, 6);
  memcpy(m.data, data, len);
  if (xQueueSend(radio_in, &m, 0) != pdTRUE)
    atomic_fetch_add(&drop_count, 1);
  wake_worker();
}
static void sent(const esp_now_send_info_t *info,
                 esp_now_send_status_t status) {
  (void)info;
  (void)status;
  atomic_store(&radio_busy, false);
  wake_worker();
}
static bool record_valid(void) {
  return !memcmp(record, "DB01", 4) &&
         (record[4] == DUAL_A || record[4] == DUAL_B) && !(record[5] & 1) &&
         memcmp(record + 5, mac, 6);
}
static bool save_pair(void) {
  uint8_t next[48] = {0};
  memcpy(next, "DB01", 4);
  next[4] = role;
  memcpy(next + 5, pair.peer, 6);
  memcpy(next + 11, pair.key, 32);
  nvs_handle_t h;
  if (nvs_open("can_dual", NVS_READWRITE, &h) != ESP_OK)
    return false;
  bool ok = nvs_set_blob(h, "peer", next, sizeof(next)) == ESP_OK &&
            nvs_commit(h) == ESP_OK;
  nvs_close(h);
  if (ok) {
    if (record_valid() && memcmp(record + 5, next + 5, 6))
      esp_now_del_peer(record + 5);
    memcpy(record, next, sizeof(record));
  }
  dual_zero(next, sizeof(next));
  return ok;
}
static bool erase_pair(void) {
  nvs_handle_t h;
  if (nvs_open("can_dual", NVS_READWRITE, &h) != ESP_OK)
    return false;
  esp_err_t e = nvs_erase_key(h, "peer");
  bool ok =
      (e == ESP_OK || e == ESP_ERR_NVS_NOT_FOUND) && nvs_commit(h) == ESP_OK;
  nvs_close(h);
  if (ok) {
    if (record_valid())
      esp_now_del_peer(record + 5);
    dual_zero(record, sizeof(record));
    dual_pair_clear(&pair);
    pair_saved = false;
  }
  return ok;
}
static void restore_bound(void) {
  purge();
  bound = record_valid() && record[4] == role;
  if (bound && !dual_session_start(&session, role, record + 11)) {
    bound = false;
    show_error = true;
  }
  next_hello = 0;
}
static void cancel_pair(void) {
  if (pair.selected && (!bound || memcmp(pair.peer, record + 5, 6)))
    esp_now_del_peer(pair.peer);
  dual_pair_clear(&pair);
  pair_saved = false;
  candidate_count = 0;
  candidate_index = 0;
  show_unbind = false;
  clear_selected = false;
  show_error = false;
  restore_bound();
}
static void begin_pair(uint32_t now) {
  cancel_pair();
  purge();
  show_error = !dual_pair_begin(&pair, role, mac, now);
  next_pair = 0;
}
static void button(int value, uint32_t now) {
  if (value == DUAL_BUTTON_CANCEL) {
    if (pair.active && !pair_saved)
      cancel_pair();
    show_unbind = false;
    clear_selected = false;
    show_error = false;
    return;
  }
  if (show_unbind) {
    if (value == DUAL_BUTTON_MIDDLE) {
      show_error = !erase_pair();
      show_unbind = false;
      restore_bound();
    }
    return;
  }
  if (pair.active && !pair_saved) {
    if (pair.derived) {
      if (value == DUAL_BUTTON_MIDDLE)
        pair.local_confirmed = true;
    } else if (role == DUAL_A && !pair.selected && candidate_count) {
      if (value == DUAL_BUTTON_MIDDLE) {
        candidate_t *c = &candidates[candidate_index];
        if ((uint32_t)(now - c->seen) < 3000)
          dual_pair_select(&pair, c->mac, c->commit);
      } else
        candidate_index =
            (candidate_index + candidate_count + (value < 0 ? -1 : 1)) %
            candidate_count;
    }
    next_pair = 0;
    return;
  }
  if (value == DUAL_BUTTON_MIDDLE) {
    if (clear_selected && bound)
      show_unbind = true;
    else
      begin_pair(now);
  } else if (bound)
    clear_selected = !clear_selected;
}
static void discover(const radio_message_t *m, uint32_t now) {
  if (role != DUAL_A || !pair.active || pair.selected || m->size != 40 ||
      m->data[4] != DUAL_BEACON || !dual_packet_valid(m->data, m->size, role))
    return;
  unsigned index = 0;
  while (index < candidate_count && memcmp(candidates[index].mac, m->mac, 6))
    index++;
  if (index == CANDIDATES)
    return;
  if (index == candidate_count)
    candidate_count++;
  memcpy(candidates[index].mac, m->mac, 6);
  memcpy(candidates[index].commit, m->data + 8, 32);
  candidates[index].seen = now;
}
static void consume_radio(const radio_message_t *m, uint32_t now) {
  if (!dual_packet_valid(m->data, m->size, role))
    return;
  if (pair.active && m->data[4] <= DUAL_CONFIRM) {
    discover(m, now);
    unsigned before = pair.selected + 2 * pair.committed + 4 * pair.derived +
                      8 * pair.remote_confirmed;
    dual_pair_receive(&pair, m->mac, m->data, m->size);
    unsigned after = pair.selected + 2 * pair.committed + 4 * pair.derived +
                     8 * pair.remote_confirmed;
    if (after != before)
      next_pair = 0;
    return;
  }
  if (!bound || (pair.active && !pair_saved) || memcmp(m->mac, record + 5, 6))
    return;
  if (m->data[4] == DUAL_HELLO) {
    uint32_t old = session.generation;
    bool was_online = session.online;
    if (dual_session_receive_hello(&session, m->data, m->size, now) &&
        !was_online)
      next_hello = 0;
    if (old != session.generation)
      purge();
    return;
  }
  uint8_t plain[190];
  size_t size = 0;
  uint32_t seq = 0;
  if (!dual_session_decode(&session, m->data, m->size, &seq, plain, &size))
    return;
  if (m->data[4] == DUAL_ACK) {
    if (size == 0 && pending_size && seq == pending_sequence) {
      atomic_fetch_add(&sent_count, pending_count);
      pending_size = 0;
      pending_count = 0;
      session.last_seen = now;
    }
    return;
  }
  if (live_uart != atomic_load(&requested_uart)) return;
  if (m->data[4] == DUAL_UART) {
    if (!live_uart || !size || size>DUAL_UART_CHUNK) return;
    if (seq <= session.rx_sequence) {
      if (seq==session.rx_sequence) ack_sequence=seq;
      return;
    }
    uart_message_t chunk={.size=size,.created=now,.generation=atomic_load(&generation)};
    memcpy(chunk.data,plain,size);
    QueueHandle_t destination=uart_in;
#if CAN_DUAL_UART_LOOPBACK_TEST
    // Test B only: admit directly to the ordinary wireless TX queue. ACK only
    // after admission, so congestion/retry/dedup/TTL retain their usual meaning.
    // A still delivers the return bytes to its physical UART. No wire change.
    if (role==DUAL_B) destination=uart_out;
#endif
    if (xQueueSend(destination,&chunk,0)!=pdTRUE) return;
    session.rx_sequence=seq;session.last_seen=now;ack_sequence=seq;
    atomic_fetch_add(&received_count,size);
    if (destination==uart_in && uart_owner) xTaskNotifyGive(uart_owner);
    return;
  }
  if (live_uart) return;
  if (!size || plain[0] == 0 || plain[0] > DUAL_BATCH ||
      size != 1 + (size_t)plain[0] * DUAL_CAN_SIZE)
    return;
  for (unsigned i = 0; i < plain[0]; i++)
    if (!dual_can_valid(plain + 1 + i * DUAL_CAN_SIZE))
      return;
  if (seq <= session.rx_sequence) {
    if (seq == session.rx_sequence)
      ack_sequence = seq;
    return;
  }
  // Admit a complete batch atomically with respect to this sole producer.
  // Without room, withhold ACK so the sender can retry within its deadline.
  if (uxQueueSpacesAvailable(can_in) < plain[0])
    return;
  for (unsigned i = 0; i < plain[0]; i++) {
    can_message_t frame = {.created = now,
                           .generation = atomic_load(&generation)};
    memcpy(frame.data, plain + 1 + i * DUAL_CAN_SIZE, DUAL_CAN_SIZE);
    xQueueSend(can_in, &frame, 0);
  }
  session.rx_sequence = seq;
  session.last_seen = now;
  ack_sequence = seq;
  atomic_fetch_add(&received_count, plain[0]);
  if (can_owner)
    xTaskNotifyGive(can_owner);
}
static void prepare_can(uint32_t now) {
  if (pending_size || !session.online)
    return;
  uint8_t plain[1 + DUAL_BATCH * DUAL_CAN_SIZE] = {0};
  can_message_t m;
  uint32_t oldest = now;
  while (plain[0] < DUAL_BATCH && xQueueReceive(can_out, &m, 0) == pdTRUE) {
    if (m.generation != atomic_load(&generation) ||
        (uint32_t)(now - m.created) >= DUAL_FRAME_TTL_MS) {
      atomic_fetch_add(&drop_count, 1);
      continue;
    }
    if (!plain[0])
      oldest = m.created;
    memcpy(plain + 1 + plain[0] * DUAL_CAN_SIZE, m.data, DUAL_CAN_SIZE);
    plain[0]++;
  }
  if (!plain[0])
    return;
  if (session.tx_sequence == UINT32_MAX) {
    atomic_fetch_add(&drop_count, plain[0]);
    restore_bound();
    return;
  }
  pending_sequence = ++session.tx_sequence;
  pending_size =
      dual_session_encode(&session, DUAL_DATA, pending_sequence, plain,
                          1 + plain[0] * DUAL_CAN_SIZE, pending);
  if (!pending_size) {
    atomic_fetch_add(&drop_count, plain[0]);
    return;
  }
  pending_count = plain[0];
  pending_type = DUAL_DATA;
  pending_at = oldest;
  attempts = 0;
  next_retry = 0;
}
static bool uart_prepare_current(uint32_t epoch) {
  return session.online && live_uart && atomic_load(&requested_uart) &&
         !atomic_load(&reset_data) && dual_link_connected() &&
         epoch == atomic_load(&generation);
}
static void prepare_uart(uint32_t now) {
  _Static_assert(DUAL_UART_CHUNK <= 190 && DUAL_UART_CHUNK + 60 <= DUAL_MAX_PACKET,
                 "UART payload must fit the session and ESP-NOW packet limits");
  if (pending_size || !session.online) return;
  const uint32_t epoch = atomic_load(&generation);
  uart_message_t combined = {0}, next;
  // Snapshot the work budget: never wait for, or chase, newly arriving bytes.
  unsigned queued = uxQueueMessagesWaiting(uart_out);
  while (queued-- && uart_prepare_current(epoch) &&
         xQueuePeek(uart_out, &next, 0) == pdTRUE) {
    // Peek leaves a non-fitting item at the head, preserving FIFO order.
    // A generation boundary ends this packet even if later items match again.
    if (combined.size && (next.generation != epoch ||
                          next.size > DUAL_UART_CHUNK - combined.size))
      break;
    if (xQueueReceive(uart_out, &next, 0) != pdTRUE) break;
    if (!uart_prepare_current(epoch)) {
      atomic_fetch_add(&uart_drop_count, next.size);
      break;
    }
    if (next.generation != epoch || !next.size || next.size > DUAL_UART_CHUNK ||
        (uint32_t)(now - next.created) >= CAN_DUAL_UART_TTL_MS) {
      atomic_fetch_add(&uart_drop_count, next.size);
      if (combined.size) break;
      continue;
    }
    if (!combined.size || (uint32_t)(now - next.created) >
                          (uint32_t)(now - combined.created))
      combined.created = next.created;
    memcpy(combined.data + combined.size, next.data, next.size);
    combined.size += next.size;
    if (combined.size == DUAL_UART_CHUNK) break;
  }
  if (!combined.size) return;
  if (!uart_prepare_current(epoch)) {
    atomic_fetch_add(&uart_drop_count, combined.size);
    return;
  }
  if (session.tx_sequence == UINT32_MAX) {
    atomic_fetch_add(&uart_drop_count, combined.size);
    restore_bound();
    return;
  }
  pending_sequence = ++session.tx_sequence;
  size_t encoded = dual_session_encode(&session, DUAL_UART, pending_sequence,
                                      combined.data, combined.size, pending);
  if (!encoded || !uart_prepare_current(epoch)) {
    atomic_fetch_add(&uart_drop_count, combined.size);
    return;
  }
  pending_type = DUAL_UART;
  pending_count = combined.size;
  pending_at = combined.created;
  attempts = 0;
  next_retry = 0;
  pending_size = encoded;
}
static void tick(uint32_t now) {
  if (atomic_exchange(&reset_data,false) || live_uart!=atomic_load(&requested_uart)) {
    live_uart=atomic_load(&requested_uart);
    restore_bound();
  }
  if (role != atomic_load(&requested_role)) {
    atomic_store(&accepting, false);
    return;
  }
  for (unsigned i = 0; i < candidate_count;) {
    if ((uint32_t)(now - candidates[i].seen) >= 3000) {
      candidates[i] = candidates[--candidate_count];
      candidate_index = 0;
    } else
      i++;
  }
  int input;
  for (unsigned i = 0; i < 4 && xQueueReceive(buttons, &input, 0) == pdTRUE;
       i++)
    button(input, now);
  radio_message_t incoming;
  for (unsigned i = 0;
       i < 16 && xQueueReceive(radio_in, &incoming, 0) == pdTRUE; i++)
    consume_radio(&incoming, now);
  if (pair.active) {
    if (!pair_saved && (uint32_t)(now - pair.started) >= DUAL_PAIR_MS) {
      cancel_pair();
      show_error = true;
    } else if (!pair_saved && dual_pair_complete(&pair)) {
      if (save_pair()) {
        pair_saved = true;
        pair_saved_at = now;
        restore_bound();
      } else {
        cancel_pair();
        show_error = true;
      }
    } else if (pair_saved && (uint32_t)(now - pair_saved_at) >= 10000) {
      dual_pair_clear(&pair);
      pair_saved = false;
    }
  }
  if (bound && session.online && (uint32_t)(now - session.last_seen) >= 1000)
    restore_bound();
  bool forwarding =
      bound && session.online && (!pair.active || pair_saved) && !show_unbind;
  bool was_forwarding=atomic_exchange(&accepting, forwarding);
  // Admission can precede the online gate in this same radio turn. Re-wake
  // output after opening the gate, so that earlier notification cannot be lost.
  if (forwarding && !was_forwarding && uart_owner) xTaskNotifyGive(uart_owner);
  if (pending_size &&
      ((uint32_t)(now - pending_at) >= (pending_type==DUAL_UART?CAN_DUAL_UART_TTL_MS:DUAL_FRAME_TTL_MS) ||
       (attempts >= (pending_type==DUAL_UART?20:RETRIES) && (int32_t)(now - next_retry) >= 0))) {
    atomic_fetch_add(pending_type==DUAL_UART?&uart_drop_count:&drop_count, pending_count);
    pending_size = 0;
    pending_count = 0;
  }
  if (atomic_load(&radio_busy))
    return;
  uint8_t out[DUAL_MAX_PACKET];
  size_t n;
  if (ack_sequence) {
    n = dual_session_encode(&session, DUAL_ACK, ack_sequence, NULL, 0, out);
    if (send_packet(record + 5, out, n))
      ack_sequence = 0;
    return;
  }
  if (pair.active && (!next_pair || (int32_t)(now - next_pair) >= 0)) {
    n = dual_pair_message(&pair, out);
    if (n && send_packet(pair.selected ? pair.peer : broadcast, out, n)) {
      next_pair = now + 200;
      return;
    }
    next_pair = now + 200;
  }
  if (bound && (!pair.active || pair_saved) &&
      (!next_hello || (int32_t)(now - next_hello) >= 0)) {
    n = dual_session_hello(&session, out);
    if (send_packet(record + 5, out, n)) {
      next_hello = now + 200;
      return;
    }
  }
  if (!forwarding)
    return;
  if (live_uart) prepare_uart(now); else prepare_can(now);
  if (pending_size && (!next_retry || (int32_t)(now - next_retry) >= 0) &&
      send_packet(record + 5, pending, pending_size)) {
    attempts++;
    next_retry = now + (pending_type==DUAL_UART?CAN_DUAL_UART_RETRY_MS:RETRY_MS);
  }
}
static void task(void *arg) {
  (void)arg;
  for (;;) {
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));
    xSemaphoreTake(lock, portMAX_DELAY);
    if (running)
      tick(now_ms());
    xSemaphoreGive(lock);
  }
}
bool dual_link_init(void) {
  lock = xSemaphoreCreateMutex();
  radio_in = xQueueCreate(RADIO_QUEUE, sizeof(radio_message_t));
  can_out = xQueueCreate(CAN_QUEUE, sizeof(can_message_t));
  can_in = xQueueCreate(CAN_QUEUE, sizeof(can_message_t));
  uart_out = xQueueCreate(UART_QUEUE,sizeof(uart_message_t));
  uart_in = xQueueCreate(UART_QUEUE,sizeof(uart_message_t));
  buttons = xQueueCreate(8, sizeof(int));
  if (!lock || !radio_in || !can_out || !can_in || !uart_out || !uart_in || !buttons)
    return false;
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  nvs_handle_t h;
  if (nvs_open("can_dual", NVS_READONLY, &h) == ESP_OK) {
    size_t n = sizeof(record);
    if (nvs_get_blob(h, "peer", record, &n) != ESP_OK || n != sizeof(record))
      dual_zero(record, sizeof(record));
    nvs_close(h);
  }
  return xTaskCreate(task, "can_dual", 8192, NULL, 3, &worker) == pdPASS;
}
void dual_link_request_mode(unsigned mode) {
  unsigned wanted = mode >= DUAL_A && mode <= DUAL_B ? mode : 0;
  if (atomic_exchange(&requested_role, wanted) != wanted) {
    atomic_store(&accepting, false);
    atomic_fetch_add(&generation, 1);
    if (uart_owner) xTaskNotifyGive(uart_owner);
    wake_worker();
  }
}
bool dual_link_start(unsigned new_role) {
  if (!worker || (new_role != DUAL_A && new_role != DUAL_B))
    return false;
  xSemaphoreTake(lock, portMAX_DELAY);
  role = new_role;
  show_error = false;
  bool ok = esp_now_init() == ESP_OK;
  if (ok)
    ok = esp_now_register_recv_cb(received) == ESP_OK &&
         esp_now_register_send_cb(sent) == ESP_OK && add_peer(broadcast);
  if (ok) {
    running = true;
    live_uart=atomic_load(&requested_uart);
    atomic_store(&live_role, new_role);
    cancel_pair();
  } else {
    esp_now_deinit();
    show_error = true;
  }
  xSemaphoreGive(lock);
  wake_worker();
  return ok;
}
void dual_link_stop(void) {
  if (!worker)
    return;
  xSemaphoreTake(lock, portMAX_DELAY);
  if (running) {
    purge();
    running = false;
    atomic_store(&live_role, 0);
    esp_now_deinit();
    dual_pair_clear(&pair);
    dual_zero(&session, sizeof(session));
    xQueueReset(radio_in);
    xQueueReset(buttons);
    atomic_store(&radio_busy, false);
  }
  role = 0;
  bound = false;
  xSemaphoreGive(lock);
}
void dual_link_set_owner(TaskHandle_t owner) { can_owner = owner; }
void dual_link_set_uart_owner(TaskHandle_t owner) { uart_owner = owner; }
void dual_link_button(int value) {
  if (buttons && xQueueSend(buttons, &value, 0) == pdTRUE)
    wake_worker();
}
bool dual_link_connected(void) {
  unsigned desired = atomic_load(&requested_role);
  return desired >= DUAL_A && desired == atomic_load(&live_role) &&
         atomic_load(&accepting);
}
uint32_t dual_link_dropped(void) { return atomic_load(&drop_count); }
void dual_link_can_rx(const uint8_t p[20]) {
  if (atomic_load(&requested_uart) || !dual_link_connected() || p[0] != 1 || p[1] != 1)
    return;
  can_message_t m = {.created = dual_u32(p + 16),
                     .generation = atomic_load(&generation)};
  if ((uint32_t)(now_ms() - m.created) >= DUAL_FRAME_TTL_MS) {
    atomic_fetch_add(&drop_count, 1);
    return;
  }
  m.data[0] = p[2];
  m.data[1] = p[3];
  memcpy(m.data + 2, p + 4, 4);
  if (!(p[2] & 2))
    memcpy(m.data + 6, p + 8, p[3] <= 8 ? p[3] : 8);
  if (!dual_can_valid(m.data) || xQueueSend(can_out, &m, 0) != pdTRUE)
    atomic_fetch_add(&drop_count, 1);
  else
    wake_worker();
}
bool dual_link_can_take(uint8_t p[20]) {
  if (atomic_load(&requested_uart) || !dual_link_connected() || !can_in)
    return false;
  can_message_t m;
  while (xQueueReceive(can_in, &m, 0) == pdTRUE) {
    if (!dual_link_connected() || m.generation != atomic_load(&generation) ||
        (uint32_t)(now_ms() - m.created) >= DUAL_FRAME_TTL_MS) {
      atomic_fetch_add(&drop_count, 1);
      continue;
    }
    memset(p, 0, 20);
    p[0] = 1;
    p[1] = 0x10;
    p[2] = m.data[0];
    p[3] = m.data[1];
    memcpy(p + 4, m.data + 2, 4);
    memcpy(p + 8, m.data + 6, 8);
    return true;
  }
  return false;
}
void dual_link_view(dual_view_t *out) {
  memset(out, 0, sizeof(*out));
  if (!worker) {
    out->state = DUAL_UI_ERROR;
    return;
  }
  xSemaphoreTake(lock, portMAX_DELAY);
  out->bound = bound;
  out->clear_selected = clear_selected;
  out->code = pair.code;
  out->confirmed = pair.local_confirmed;
  out->state = show_error    ? DUAL_UI_ERROR
               : !running    ? DUAL_UI_OFF
               : show_unbind ? DUAL_UI_UNBIND
               : pair.active && !pair_saved
                   ? (pair.derived     ? DUAL_UI_CHECK
                      : pair.selected  ? DUAL_UI_HANDSHAKE
                      : role == DUAL_A ? DUAL_UI_SEARCH
                                       : DUAL_UI_WAIT)
               : bound ? (session.online ? DUAL_UI_ONLINE : DUAL_UI_OFFLINE)
                       : DUAL_UI_UNBOUND;
  if (pair.active && !pair.selected && role == DUAL_A && candidate_count) {
    memcpy(out->peer, candidates[candidate_index].mac, 6);
    out->count = candidate_count;
    out->index = candidate_index;
  } else if (pair.selected)
    memcpy(out->peer, pair.peer, 6);
  else if (bound)
    memcpy(out->peer, record + 5, 6);
  out->sent = atomic_load(&sent_count);
  out->received = atomic_load(&received_count);
  out->dropped = atomic_load(&drop_count);
  xSemaphoreGive(lock);
}

void dual_link_set_uart(bool enabled) {
  if (atomic_exchange(&requested_uart,enabled)!=enabled) {
    atomic_store(&accepting,false);
    atomic_fetch_add(&generation,1);
    if (uart_owner) xTaskNotifyGive(uart_owner);
    wake_worker();
  }
}
uint32_t dual_link_generation(void) { return atomic_load(&generation); }
uint32_t dual_link_uart_dropped(void) { return atomic_load(&uart_drop_count); }
bool dual_link_uart_rx(const uint8_t *data,size_t size) {
  if (!data || !size || size>DUAL_UART_CHUNK || !uart_out ||
      !atomic_load(&requested_uart) || !dual_link_connected()) return false;
  uart_message_t m={.size=size,.created=now_ms(),.generation=atomic_load(&generation)};
  memcpy(m.data,data,size);
  if (xQueueSend(uart_out,&m,0)!=pdTRUE) return false;
  wake_worker();return true;
}
bool dual_link_uart_take(uint8_t *data,size_t *size) {
  if (!data || !size || !uart_in || !atomic_load(&requested_uart) || !dual_link_connected()) return false;
  uart_message_t m;
  while (xQueueReceive(uart_in,&m,0)==pdTRUE) {
    if (!atomic_load(&requested_uart) || !dual_link_connected() ||
        m.generation!=atomic_load(&generation) || (uint32_t)(now_ms()-m.created)>=CAN_DUAL_UART_TTL_MS) {
      atomic_fetch_add(&uart_drop_count,m.size);continue;
    }
    memcpy(data,m.data,m.size);*size=m.size;return true;
  }
  return false;
}

void dual_link_reset_data(void){atomic_store(&accepting,false);atomic_fetch_add(&generation,1);atomic_store(&reset_data,true);if(uart_owner)xTaskNotifyGive(uart_owner);wake_worker();}
