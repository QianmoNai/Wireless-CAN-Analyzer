#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Numeric STATUS ABI. -1 means no disconnect event in this APPLY (or no APPLY).
 * The owner serializes all access. Only explicit APPLY opens a capture window;
 * finish closes it BEFORE disconnect/stop, scanning or background reconnect. */
enum { NET_STAGE_NONE, NET_STAGE_SET_CONFIG, NET_STAGE_START, NET_STAGE_CONNECT,
       NET_STAGE_WAIT_IP, NET_STAGE_SAVE };
enum { NETWORK_STATUS_JSON_LIMIT=512 };
typedef struct {
    int32_t reason, api_err;
    uint8_t stage, got_ip;
    bool active;
    uint32_t attempt_id;
    uint64_t began_ms; /* esp_timer monotonic milliseconds, not wall-clock time */
    int64_t start_ms, connected_ms, ip_ms; /* offsets from began_ms; -1 = absent */
    uint16_t disconnect_count, ble_interval; /* interval in 1.25 ms units; 0 unknown */
    uint8_t bssid[6], sta_mac[6], channel; /* channel 0 unknown */
    int8_t rssi, tx_power; /* -128 unknown; TX power is in 0.25 dBm units */
    char country[3]; /* two ASCII letters, or world-safe "01"; empty unknown */
} network_diagnostics_t;
void network_diagnostics_begin(network_diagnostics_t *d, uint64_t now_ms);
void network_diagnostics_api(network_diagnostics_t *d, uint8_t stage, int32_t err);
void network_diagnostics_start(network_diagnostics_t *d, uint64_t now_ms);
void network_diagnostics_connected(network_diagnostics_t *d, uint64_t now_ms,
                                   const uint8_t bssid[6], uint8_t channel);
void network_diagnostics_disconnect(network_diagnostics_t *d, uint8_t reason,
                                    const uint8_t bssid[6], int8_t rssi);
void network_diagnostics_got_ip(network_diagnostics_t *d, uint64_t now_ms);
void network_diagnostics_finish(network_diagnostics_t *d);

/* Same protected STATUS fields as before; only the compact diag array is new.
 * The caller supplies existing bounded hex strings, never arbitrary JSON text. */
typedef struct {
    uint8_t state, error;
    bool busy, mode;
    const char *ip, *ssid, *id, *fp, *token;
} network_status_t;
size_t network_status_json(char *out, size_t size, const network_status_t *s,
                           const network_diagnostics_t *d);

typedef struct {
    uint8_t ssid[32];
    int rssi, authmode, primary;
} network_scan_row_t;
/* At most three rows per page; old clients consume the first three columns. */
size_t network_scan_page_json(char *out, size_t size, const network_scan_row_t *rows,
                              unsigned count, unsigned total, unsigned offset);
