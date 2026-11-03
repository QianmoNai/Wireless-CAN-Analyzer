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

// Diagnostic v2 suffix, test builds only. Lifetime independently sampled words.
// event.size is meaningful only for UART_DATA; overflow events carry no byte count.
enum { UD_DATA_EVENTS, UD_DATA_EVENT_BYTES, UD_FIFO_OVF, UD_BUFFER_FULL,
    UD_FRAME_ERR, UD_PARITY_ERR, UD_READ_CALLS, UD_READ_BYTES,
    UD_BLE_ATTEMPT_BYTES, UD_BLE_ACCEPT_BYTES, UD_BLE_REJECT_BYTES,
    UD_DISCONNECTED_BYTES, UD_DUAL_BYTES, UD_CONFIG_FLUSHES, UD_CONFIG_FLUSH_BYTES,
    UD_ERROR_FLUSHES, UD_ERROR_FLUSH_BYTES, UD_TX_DISCARD_BYTES,
    UD_DOWNSTREAM_DROP_BYTES, UD_BUFFER_QUERY_ERRORS, UD_FLUSH_ERRORS,
    UD_READ_ERRORS, UD_EVENT_HIGH_WATER, UD_WORDS };
// Defined and referenced only when BLE_TEST_DIAGNOSTICS is enabled.
void uart_bridge_diagnostics(uint32_t out[UD_WORDS]);
