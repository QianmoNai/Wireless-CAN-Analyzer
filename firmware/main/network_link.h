#pragma once
#include "ble_bridge.h"
#include <stddef.h>
void network_init(void);
// 0 BLE, 1 LAN Wi-Fi, 2 dual A, 3 dual B.
void network_set_mode(unsigned mode);
bool network_dual_mode(void);
bool network_wifi_mode(void);
bool network_connected(void);
bool network_send(const bridge_packet_t *packet);
// Call only after BLE MITM authentication is checked; packet length is exactly 20.
int network_config_write(const uint8_t packet[20]);
size_t network_config_read(char *json, size_t capacity);
void network_config_disconnect(void);
void network_pairing_code(uint32_t code);
void network_pairing_end(void);
// Thread-safe local display status. pairing==UINT32_MAX means no active pairing.
void network_display(uint8_t *state, char ip[16], uint32_t *pairing);
