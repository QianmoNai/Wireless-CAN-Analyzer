#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Numeric STATUS ABI. -1 means no disconnect event in this APPLY (or no APPLY).
 * The owner serializes all access. Only explicit APPLY opens a capture window;
 * finish closes it BEFORE disconnect/stop, scanning or background reconnect. */
enum { NET_STAGE_NONE, NET_STAGE_SET_CONFIG, NET_STAGE_START, NET_STAGE_CONNECT,
       NET_STAGE_WAIT_IP, NET_STAGE_SAVE };
typedef struct {
    int32_t reason, api_err;
    uint8_t stage, got_ip;
    bool active;
} network_diagnostics_t;
void network_diagnostics_begin(network_diagnostics_t *d);
void network_diagnostics_api(network_diagnostics_t *d, uint8_t stage, int32_t err);
void network_diagnostics_disconnect(network_diagnostics_t *d, unsigned reason);
void network_diagnostics_got_ip(network_diagnostics_t *d);
void network_diagnostics_finish(network_diagnostics_t *d);

typedef struct {
    uint8_t ssid[32];
    int rssi, authmode, primary;
} network_scan_row_t;
/* At most three rows per page; old clients consume the first three columns. */
size_t network_scan_page_json(char *out, size_t size, const network_scan_row_t *rows,
                              unsigned count, unsigned total, unsigned offset);
