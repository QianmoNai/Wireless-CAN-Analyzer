// Compile the actual runtime twice to simulate two independent ESP32 devices.
#define dual_link_usb_command dual_b_usb_command
#define dual_link_init dual_b_init
#define dual_link_start dual_b_start
#define dual_link_stop dual_b_stop
#define dual_link_request_mode dual_b_request_mode
#define dual_link_set_uart_owner dual_b_set_uart_owner
#define dual_link_set_owner dual_b_set_owner
#define dual_link_button dual_b_button
#define dual_link_view dual_b_view
#define dual_link_connected dual_b_connected
#define dual_link_can_rx dual_b_can_rx
#define dual_link_can_take dual_b_can_take
#define dual_link_dropped dual_b_dropped
#define dual_link_set_uart dual_b_set_uart
#define dual_link_reset_data dual_b_reset_data
#define dual_link_generation dual_b_generation
#define dual_link_uart_rx dual_b_uart_rx
#define dual_link_uart_take dual_b_uart_take
#define dual_link_uart_dropped dual_b_uart_dropped
#include "../firmware/main/dual_link.c"
void dual_b_tick(void) {
  if (running)
    tick(now_ms());
}
void dual_b_destroy(void) { dual_link_stop(); }
