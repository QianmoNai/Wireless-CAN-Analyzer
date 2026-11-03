#pragma once
#include "uart_protocol.h"
typedef struct { uint32_t rx,tx,dropped,errors,baud; bool ready; } uart_bridge_stats_t;
void uart_bridge_init(void);
void uart_bridge_configure(bool selected,uint32_t baud);
bool uart_bridge_selected(void);
bool uart_bridge_send(const uint8_t packet[20]);
void uart_bridge_disconnect(void);
void uart_bridge_stats(uart_bridge_stats_t *out);
// Only the current owner of an admitted downstream packet calls this on final
// rejection/discard. A send() returning false leaves accounting to its caller.
// Counts valid type19 payload bytes, never type20 completion acknowledgements.
void uart_bridge_drop_packet(const bridge_packet_t *packet);
