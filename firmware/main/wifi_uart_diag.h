#pragma once
#include "uart_protocol.h"
#include <stddef.h>

/* TLS-only extension to QUERY (0x19/op=2). BLE and CUSB keep rejecting op=2.
 * No credentials/payload are collected. Lifetime uint32 counters wrap; every
 * record is {value, last_change_uptime_ms, last_change_UART_sequence}.
 * FIFO/ring lost bytes and private Wi-Fi/TCP statistics are NOT estimable.
 */
enum {
    WD_SAMPLE_MS, WD_MODE, WD_UART_SEQUENCE, WD_UART_RX_BYTES, WD_UART_DROP_BYTES,
    WD_FIFO_OVERRUN_EVENTS, WD_RING_HIGH_BYTES, WD_RING_FULL_EVENTS, WD_RING_LOST_BYTES,
    WD_UART_READ_ERRORS, WD_CONFIG_FLUSH_BYTES, WD_DISCONNECTED_BYTES, WD_TX_DISCARD_BYTES,
    WD_NOTIFY_RAW_DEPTH, WD_NOTIFY_RAW_HIGH, WD_NOTIFY_CONTROL_DEPTH, WD_NOTIFY_CONTROL_HIGH,
    WD_NOTIFY_ACK_DEPTH, WD_NOTIFY_ACK_HIGH,
    WD_DROP_NOTIFY_FULL, WD_DROP_NOTIFY_RESET, WD_DROP_GENERATION, WD_DROP_INACTIVE,
    WD_DROP_SUPPRESSED, WD_DROP_ROUTE,
    WD_LAN_RAW_DEPTH, WD_LAN_RAW_HIGH, WD_LAN_CONTROL_DEPTH, WD_LAN_CONTROL_HIGH,
    WD_LAN_REJECT_FULL, WD_LAN_REJECT_OFFLINE, WD_DROP_LAN_RESET, WD_DROP_TLS,
    WD_TLS_SEND_CALLS, WD_TLS_SEND_FAILURES, WD_TLS_SEND_TIMEOUTS, WD_TLS_WANT_EVENTS,
    WD_TLS_BLOCK_US_TOTAL, WD_TLS_BLOCK_US_MAX, WD_TLS_CALL_US_TOTAL, WD_TLS_CALL_US_MAX,
    WD_TLS_PENDING_HIGH_BYTES, WD_TCP_KERNEL_SEND_HIGH_BYTES,
    WD_TCP_CLOSES, WD_TCP_CLOSE_REASON, WD_TCP_READ_FAILURES,
    WD_TLS_LAST_ERROR, WD_SOCKET_LAST_ERRNO, WD_TCP_NODELAY, WD_TLS_HANDSHAKE_FAILURES,
    WD_WIFI_RSSI, WD_WIFI_RATE_KBPS, WD_WIFI_RETRIES,
    WD_WIFI_DISCONNECTS, WD_WIFI_REASON, WD_WIFI_PHY_MODE,
    WD_FIELDS
};
enum { WD_CLOSE_NONE, WD_CLOSE_MODE, WD_CLOSE_WRITE, WD_CLOSE_EOF,
       WD_CLOSE_READ, WD_CLOSE_HEARTBEAT, WD_CLOSE_DIAGNOSTIC, WD_CLOSE_OPTIONS };
enum { WD_QUERY=0x19, WD_QUERY_OP=2, WD_EVENT=26, WD_HEADER=255, WD_VERSION=1 };
#define WD_UNKNOWN UINT32_MAX
#define WD_NOW_SEQUENCE UINT32_MAX
typedef struct { uint32_t value, at_ms, uart_sequence; } wifi_diag_record_t;
typedef struct {
    uint32_t boot_id, serial;
    wifi_diag_record_t records[WD_FIELDS];
} wifi_diag_snapshot_t;
_Static_assert((unsigned)WD_FIELDS<(unsigned)WD_HEADER, "bounded diagnostic frame indices");

void wifi_diag_boot(uint32_t public_boot_id);
void wifi_diag_mode(unsigned mode);
void wifi_diag_add(unsigned field,uint32_t n,uint32_t sequence);
void wifi_diag_set(unsigned field,uint32_t value,uint32_t sequence);
void wifi_diag_max(unsigned field,uint32_t value);
void wifi_diag_queue(unsigned depth_field,unsigned high_field,unsigned depth);
void wifi_diag_drop(unsigned field,const bridge_packet_t *packet);
void wifi_diag_snapshot(wifi_diag_snapshot_t *out);
bool wifi_diag_query_valid(const uint8_t p[20]);
/* One header + fixed-count records. The existing 512 B provisioning JSON is
 * untouched. The reply is carried by existing authenticated TLS 20 B framing. */
size_t wifi_diag_frames(const wifi_diag_snapshot_t *s,uint32_t token,
                        uint8_t *out,size_t capacity);
