#include "dual_protocol.h"
#include "dual_view.h"
#include "sim.h"
#include <stdio.h>
#define DECLARE(n)                                                             \
  bool dual_##n##_init(void);                                                  \
  bool dual_##n##_start(unsigned role);                                        \
  void dual_##n##_stop(void);                                                  \
  void dual_##n##_request_mode(unsigned mode);                                 \
  void dual_##n##_button(int b);                                               \
  void dual_##n##_view(dual_view_t *v);                                        \
  bool dual_##n##_connected(void);                                             \
  void dual_##n##_can_rx(const uint8_t p[20]);                                 \
  bool dual_##n##_can_take(uint8_t p[20]);                                     \
  void dual_##n##_set_uart(bool enabled);                                      \
  void dual_##n##_reset_data(void);                                           \
  bool dual_##n##_uart_rx(const uint8_t *data,size_t size);                     \
  bool dual_##n##_uart_take(uint8_t *data,size_t *size);                         \
  uint32_t dual_##n##_uart_dropped(void);                                      \
  void dual_##n##_tick(void);
DECLARE(a) DECLARE(b) static int current;
static uint32_t time_ms;
static recv_cb_t rx[2];
static send_cb_t tx[2];
static bool active[2];
static const uint8_t macs[2][6] = {{2, 1, 2, 3, 4, 5}, {2, 1, 2, 3, 4, 6}};
static uint8_t storage[2][48];
static bool stored[2], fail_nvs[2], cut_radio;
static unsigned drop_ack;
struct queue {
  unsigned count, limit;
  size_t size;
  uint8_t *data;
};
static struct queue *queues[16];
static unsigned queue_count;
QueueHandle_t xQueueCreate(unsigned n, size_t size) {
  struct queue *q = calloc(1, sizeof(*q));
  q->limit = n;
  q->size = size;
  q->data = calloc(n, size);
  queues[queue_count++] = q;
  return q;
}
int xQueueSend(QueueHandle_t q, const void *p, unsigned wait) {
  (void)wait;
  if (q->count == q->limit)
    return 0;
  memcpy(q->data + q->size * q->count++, p, q->size);
  return 1;
}
int xQueueReceive(QueueHandle_t q, void *p, unsigned wait) {
  (void)wait;
  if (!q->count)
    return 0;
  memcpy(p, q->data, q->size);
  memmove(q->data, q->data + q->size, --q->count * q->size);
  return 1;
}
void xQueueReset(QueueHandle_t q) { q->count = 0; }
unsigned uxQueueMessagesWaiting(QueueHandle_t q) { return q->count; }
unsigned uxQueueSpacesAvailable(QueueHandle_t q) { return q->limit - q->count; }
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
int xSemaphoreTake(SemaphoreHandle_t s, unsigned w) {
  (void)s;
  (void)w;
  return 1;
}
void xSemaphoreGive(SemaphoreHandle_t s) { (void)s; }
int xTaskCreate(void (*f)(void *), const char *n, unsigned st, void *a,
                unsigned pr, TaskHandle_t *h) {
  (void)f;
  (void)n;
  (void)st;
  (void)a;
  (void)pr;
  *h = (void *)1;
  return 1;
}
void xTaskNotifyGive(TaskHandle_t t) { (void)t; }
unsigned ulTaskNotifyTake(int c, unsigned w) {
  (void)c;
  (void)w;
  return 0;
}
int64_t esp_timer_get_time(void) { return (int64_t)time_ms * 1000; }
void esp_read_mac(uint8_t *mac, int t) {
  (void)t;
  memcpy(mac, macs[current], 6);
}
int esp_now_init(void) {
  active[current] = true;
  return 0;
}
int esp_now_deinit(void) {
  active[current] = false;
  return 0;
}
int esp_now_register_recv_cb(recv_cb_t cb) {
  rx[current] = cb;
  return 0;
}
int esp_now_register_send_cb(send_cb_t cb) {
  tx[current] = cb;
  return 0;
}
bool esp_now_is_peer_exist(const uint8_t *mac) {
  (void)mac;
  return true;
}
int esp_now_add_peer(const esp_now_peer_info_t *p) {
  (void)p;
  return 0;
}
int esp_now_del_peer(const uint8_t *mac) {
  (void)mac;
  return 0;
}
static uint8_t captured[250];
static size_t captured_size;
static unsigned transmissions[10];
typedef struct {
  bool pending, drop;
  uint32_t due;
  size_t size;
  uint8_t data[250];
} air_frame_t;
static air_frame_t air[2];
int esp_now_send(const uint8_t *mac, const uint8_t *p, size_t n) {
  (void)mac;
  assert(n <= 250 && !air[current].pending);
  transmissions[p[4]]++;
  bool drop = cut_radio;
  if (p[4] == DUAL_ACK && drop_ack) {
    drop = true;
    drop_ack--;
  }
  if (p[4] == DUAL_DATA && current == 0) {
    memcpy(captured, p, n);
    captured_size = n;
  }
  air[current] = (air_frame_t){
      .pending = true, .drop = drop, .due = time_ms + 1, .size = n};
  memcpy(air[current].data, p, n);
  return 0;
}
static void deliver_air(void) {
  for (int source = 0; source < 2; source++) {
    air_frame_t *f = &air[source];
    if (!f->pending || (int32_t)(time_ms - f->due) < 0)
      continue;
    int destination = 1 - source;
    f->pending = false;
    if (!f->drop && active[destination]) {
      current = destination;
      esp_now_recv_info_t info = {.src_addr = macs[source]};
      rx[destination](&info, f->data, f->size);
    }
    if (active[source]) {
      current = source;
      tx[source](NULL, ESP_NOW_SEND_SUCCESS);
    }
  }
}
int nvs_open(const char *n, int m, nvs_handle_t *h) {
  (void)n;
  (void)m;
  *h = current;
  return 0;
}
int nvs_get_blob(nvs_handle_t h, const char *k, void *p, size_t *n) {
  (void)k;
  if (!stored[h])
    return 1;
  assert(*n >= 48);
  memcpy(p, storage[h], 48);
  *n = 48;
  return 0;
}
int nvs_set_blob(nvs_handle_t h, const char *k, const void *p, size_t n) {
  (void)k;
  assert(n == 48);
  if (fail_nvs[h])
    return 2;
  memcpy(storage[h], p, 48);
  stored[h] = true;
  return 0;
}
int nvs_erase_key(nvs_handle_t h, const char *k) {
  (void)k;
  if (fail_nvs[h])
    return 2;
  stored[h] = false;
  memset(storage[h], 0, 48);
  return 0;
}
int nvs_commit(nvs_handle_t h) { return fail_nvs[h] ? 2 : 0; }
void nvs_close(nvs_handle_t h) { (void)h; }
static void ticks(unsigned ms) {
  for (unsigned i = 0; i < ms; i++) {
    time_ms++;
    deliver_air();
    current = 0;
    dual_a_tick();
    current = 1;
    dual_b_tick();
  }
}
static void press(int node, int key) {
  current = node;
  if (node)
    dual_b_button(key);
  else
    dual_a_button(key);
  ticks(10);
}
static dual_view_t view(int node) {
  current = node;
  dual_view_t v;
  if (node)
    dual_b_view(&v);
  else
    dual_a_view(&v);
  return v;
}
static void start(int node) {
  current = node;
  if (node) {
    dual_b_request_mode(DUAL_B);
    assert(dual_b_start(DUAL_B));
  } else {
    dual_a_request_mode(DUAL_A);
    assert(dual_a_start(DUAL_A));
  }
}
static void pair_devices(void) {
  press(1, DUAL_BUTTON_MIDDLE);
  press(0, DUAL_BUTTON_MIDDLE);
  ticks(500);
  assert(view(0).state == DUAL_UI_SEARCH && view(0).count == 1);
  assert(view(1).state == DUAL_UI_WAIT);
  press(0, DUAL_BUTTON_MIDDLE);
  ticks(1500);
  assert(view(0).state == DUAL_UI_CHECK && view(1).state == DUAL_UI_CHECK &&
         view(0).code == view(1).code);
  press(0, DUAL_BUTTON_MIDDLE);
  ticks(1000);
  assert(!dual_a_connected() && !dual_b_connected());
  press(1, DUAL_BUTTON_MIDDLE);
  ticks(2000);
  assert(dual_a_connected() && dual_b_connected());
}
static void input(int node, uint32_t id, uint8_t flags, uint8_t dlc) {
  uint8_t p[20] = {1, 1, flags, dlc};
  dual_put32(p + 4, id);
  dual_put32(p + 16, time_ms);
  for (unsigned i = 0; i < 8; i++)
    p[8 + i] = i + 1;
  current = node;
  if (node)
    dual_b_can_rx(p);
  else
    dual_a_can_rx(p);
}
static bool take(int node, uint8_t out[20]) {
  current = node;
  return node ? dual_b_can_take(out) : dual_a_can_take(out);
}
int main(void) {
  current = 0;
  assert(dual_a_init());
  current = 1;
  assert(dual_b_init());
  start(0);
  start(1);
  pair_devices();
  assert(stored[0] && stored[1] &&
         !memcmp(storage[0] + 11, storage[1] + 11, 32));
  // Bidirectional bridge preserves standard/extended IDs, RTR and DLC.
  input(0, 0x123, 0, 8);
  input(1, 0x1abcde, 1, 3);
  ticks(40);
  uint8_t p[20];
  assert(take(1, p) && dual_u32(p + 4) == 0x123 && p[2] == 0 && p[3] == 8 &&
         p[8] == 1);
  assert(take(0, p) && dual_u32(p + 4) == 0x1abcde && p[2] == 1 && p[3] == 3 &&
         p[10] == 3);
  assert(!take(0, p) && !take(1, p));
  // Age starts at CAN reception, not when the RX task finally handles it.
  uint8_t stale[20] = {1, 1, 0, 1};
  dual_put32(stale + 4, 0x125);
  dual_put32(stale + 16, time_ms - DUAL_FRAME_TTL_MS);
  current = 0;
  dual_a_can_rx(stale);
  ticks(30);
  assert(!take(1, p));
  input(0, 0x400, 2, 8);
  ticks(30);
  assert(take(1, p) && p[2] == 2 && p[3] == 8);
  for (unsigned i = 8; i < 16; i++)
    assert(!p[i]);
  // Lost ACK forces retransmission, without duplicating CAN delivery.
  unsigned before = transmissions[DUAL_DATA];
  drop_ack = 1;
  input(0, 0x124, 0, 1);
  ticks(70);
  assert(transmissions[DUAL_DATA] >= before + 2);
  assert(take(1, p) && dual_u32(p + 4) == 0x124);
  assert(!take(1, p));
  // Receiver admits no partial batch when its CAN queue is full.
  unsigned accepted = 0;
  for (unsigned i = 0; i < 160; i++) {
    input(0, 0x200 + i, 0, 1);
    ticks(1);
  }
  ticks(25);
  while (take(1, p))
    accepted++;
  assert(accepted <= 128);
  ticks(150);
  while (take(1, p)) {
  };
  // RX queue age is bounded even if the local CAN controller cannot transmit.
  input(0, 0x500, 0, 8);
  ticks(150);
  assert(!take(1, p));
  assert(view(1).dropped > 0);
  // Outage clears queues, reconnect never replays captured CAN from old
  // session.
  cut_radio = true;
  input(0, 0x501, 0, 8);
  ticks(1300);
  assert(!dual_a_connected() && !dual_b_connected());
  cut_radio = false;
  ticks(1600);
  assert(dual_a_connected() && dual_b_connected());
  assert(!take(1, p));
  current = 1;
  esp_now_recv_info_t old = {.src_addr = macs[0]};
  rx[1](&old, captured, captured_size);
  ticks(10);
  assert(!take(1, p));
  // Immediate mode request gates traffic before the Wi-Fi manager catches up.
  input(0, 0x502, 0, 8);
  current = 0;
  dual_a_request_mode(0);
  assert(!dual_a_connected());
  dual_a_stop();
  ticks(1100);
  assert(!dual_b_connected());
  start(0);
  ticks(1800);
  assert(dual_a_connected() && dual_b_connected());
  assert(!take(1, p));
  // Typed UART packets preserve binary bytes and cannot enter the CAN queue.
  current=0;dual_a_set_uart(true);current=1;dual_b_set_uart(true);ticks(1600);
  assert(dual_a_connected()&&dual_b_connected());
  uint8_t serial[DUAL_UART_CHUNK],received_serial[DUAL_UART_CHUNK];size_t serial_size=0;
  for(unsigned i=0;i<sizeof(serial);i++)serial[i]=(uint8_t)i;
  current=0;assert(dual_a_uart_rx(serial,sizeof(serial)));
  current=1;assert(dual_b_uart_rx(serial,13));ticks(40);
  current=1;assert(dual_b_uart_take(received_serial,&serial_size)&&serial_size==sizeof(serial)&&!memcmp(serial,received_serial,serial_size));
  current=0;assert(dual_a_uart_take(received_serial,&serial_size)&&serial_size==13&&!memcmp(serial,received_serial,13));
  assert(!take(0,p)&&!take(1,p));
  drop_ack=1;current=0;assert(dual_a_uart_rx(serial,37));ticks(80);
  current=1;assert(dual_b_uart_take(received_serial,&serial_size)&&serial_size==37);
  assert(!dual_b_uart_take(received_serial,&serial_size));
  current=0;assert(dual_a_uart_rx(serial,20));dual_a_set_uart(false);
  current=1;dual_b_set_uart(false);ticks(1600);
  assert(!take(1,p));current=1;assert(!dual_b_uart_take(received_serial,&serial_size));
  input(0,0x601,0,8);ticks(40);assert(take(1,p)&&dual_u32(p+4)==0x601);
  // Leaving the management page must not tear down an established bridge.
  press(0, DUAL_BUTTON_CANCEL);
  assert(dual_a_connected());
  // Unbind requires a separate confirmation. Cancellation keeps the record.
  ticks(11000);
  press(0, DUAL_BUTTON_NEXT);
  press(0, DUAL_BUTTON_MIDDLE);
  assert(view(0).state == DUAL_UI_UNBIND);
  press(0, DUAL_BUTTON_CANCEL);
  assert(stored[0]);
  press(0, DUAL_BUTTON_NEXT);
  press(0, DUAL_BUTTON_MIDDLE);
  press(0, DUAL_BUTTON_MIDDLE);
  assert(!stored[0] && !dual_a_connected());
  // Pairing and session scheduling also work across the millisecond wrap.
  time_ms = UINT32_MAX - 1500;
  pair_devices();
  assert(time_ms < 10000);
  // A failed persistent save must not activate a new pair or start CAN traffic.
  ticks(11000);
  press(0, DUAL_BUTTON_NEXT);
  press(0, DUAL_BUTTON_MIDDLE);
  press(0, DUAL_BUTTON_MIDDLE);
  fail_nvs[0] = true;
  press(1, DUAL_BUTTON_MIDDLE);
  press(0, DUAL_BUTTON_MIDDLE);
  ticks(500);
  press(0, DUAL_BUTTON_MIDDLE);
  ticks(1500);
  assert(view(0).state == DUAL_UI_CHECK && view(1).state == DUAL_UI_CHECK);
  press(0, DUAL_BUTTON_MIDDLE);
  press(1, DUAL_BUTTON_MIDDLE);
  ticks(2000);
  assert(view(0).state == DUAL_UI_ERROR && !stored[0] && !dual_a_connected() &&
         !dual_b_connected());
  current = 0;
  dual_a_stop();
  current = 1;
  dual_b_stop();
  for (unsigned i = 0; i < queue_count; i++) {
    free(queues[i]->data);
    free(queues[i]);
  }
  puts(
      "dual runtime: two real workers, screen pairing/persistence, "
      "bidirectional CAN, EXT/RTR, lost ACK deduplication, bounded queues/age, "
      "disconnect/replay, mode gating and unbind confirmation PASS");
}
