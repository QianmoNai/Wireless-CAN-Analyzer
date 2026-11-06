// Compile the actual runtime twice to simulate two independent ESP32 devices.
#define dual_link_usb_command dual_a_usb_command
#define dual_link_init dual_a_init
#define dual_link_start dual_a_start
#define dual_link_stop dual_a_stop
#define dual_link_request_mode dual_a_request_mode
#define dual_link_set_uart_owner dual_a_set_uart_owner
#define dual_link_set_owner dual_a_set_owner
#define dual_link_button dual_a_button
#define dual_link_view dual_a_view
#define dual_link_connected dual_a_connected
#define dual_link_can_rx dual_a_can_rx
#define dual_link_can_take_timed dual_a_can_take_timed
#define dual_link_can_take dual_a_can_take
#define dual_link_can_tx_failed dual_a_can_tx_failed
#define dual_link_dropped dual_a_dropped
#define dual_link_set_uart dual_a_set_uart
#define dual_link_reset_data dual_a_reset_data
#define dual_link_generation dual_a_generation
#define dual_link_uart_rx dual_a_uart_rx
#define dual_link_uart_take dual_a_uart_take
#define dual_link_uart_dropped dual_a_uart_dropped
#include "../firmware/main/dual_link.c"
#include "dual_freshness_tests.h"
void dual_a_tick(void) {
  if (running)
    tick(now_ms());
}
void dual_a_destroy(void) { dual_link_stop(); }
#include "dual_uart_coalescing_tests.h"

// Reproduce an admitted block whose first wake ran before the online gate.
void dual_a_test_uart_online_wake(void) {
  assert(bound && session.online && !uxQueueMessagesWaiting(uart_in));
  uart_message_t m={.size=3,.created=now_ms(),.generation=atomic_load(&generation),.data={1,2,3}};
  assert(xQueueSend(uart_in,&m,0)==pdTRUE);
  atomic_store(&accepting,false);
  tick(now_ms());
  assert(dual_link_connected());
}

void dual_a_test_can_origin_and_terminal_drop(void) {
 assert(bound&&session.online&&!uxQueueMessagesWaiting(can_in));
 uint32_t epoch=atomic_load(&generation),created=now_ms()-17,before=atomic_load(&drop_count);
 can_message_t m={.created=created,.generation=epoch,.data={0,8}};dual_put32(m.data+2,0x643);
 assert(xQueueSend(can_in,&m,0)==pdTRUE);
 uint8_t out[20];uint32_t got_created,got_epoch;
 assert(dual_link_can_take_timed(out,&got_created,&got_epoch));
 assert(got_created==created&&got_epoch==epoch&&dual_u32(out+4)==0x643&&atomic_load(&drop_count)==before);
 dual_link_can_tx_failed();assert(atomic_load(&drop_count)==before+1);
 atomic_store(&drop_count,before);
 m.created=now_ms()-DUAL_FRAME_TTL_MS;assert(xQueueSend(can_in,&m,0)==pdTRUE);
 assert(!dual_link_can_take_timed(out,&got_created,&got_epoch));assert(atomic_load(&drop_count)==before+1);
 atomic_store(&drop_count,before);
}
