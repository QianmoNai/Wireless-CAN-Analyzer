// Compile the actual runtime twice to simulate two independent ESP32 devices.
#define dual_link_init dual_a_init
#define dual_link_start dual_a_start
#define dual_link_stop dual_a_stop
#define dual_link_request_mode dual_a_request_mode
#define dual_link_set_owner dual_a_set_owner
#define dual_link_button dual_a_button
#define dual_link_view dual_a_view
#define dual_link_connected dual_a_connected
#define dual_link_can_rx dual_a_can_rx
#define dual_link_can_take dual_a_can_take
#define dual_link_dropped dual_a_dropped
#include "../firmware/main/dual_link.c"
void dual_a_tick(void) {
  if (running)
    tick(now_ms());
}
void dual_a_destroy(void) { dual_link_stop(); }
